"""Real-Postgres tests for the claim/renew/complete/fail/sweep state machine.

The zombie-write scenario (masterplan §4.2's "hardest to reason about" case)
is exercised here directly against `lease.py` rather than through a full
`Executor` drive loop: worker A claims, its lease is force-expired, worker B
claims the same row fresh and completes it, and worker A's late completion
attempt with its now-stale token must affect zero rows. No real hanging
coroutine is needed to prove this — it's a property of the SQL guard.
"""

from __future__ import annotations

from datetime import timedelta

import asyncpg
import pytest
from _db import insert_run, insert_task, task_status

from api.executor import lease

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


async def test_claim_next_returns_none_when_no_pending_tasks(postgres_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=postgres_dsn)
    try:
        run_id = await insert_run(conn)
        assert await lease.claim_next(conn, run_id) is None
    finally:
        await conn.close()


async def test_claim_next_respects_priority_order(postgres_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=postgres_dsn)
    try:
        run_id = await insert_run(conn)
        await insert_task(conn, run_id, "low", priority=10)
        await insert_task(conn, run_id, "high", priority=1)
        claimed = await lease.claim_next(conn, run_id)
        assert claimed is not None
        assert claimed.node_key == "high"
    finally:
        await conn.close()


async def test_claim_next_skips_task_with_unmet_dependency(postgres_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=postgres_dsn)
    try:
        run_id = await insert_run(conn)
        await insert_task(conn, run_id, "upstream")
        await insert_task(conn, run_id, "downstream", depends_on=["upstream"])

        claimed = await lease.claim_next(conn, run_id)
        assert claimed is not None
        assert claimed.node_key == "upstream"

        # downstream still blocked: upstream is 'running', not 'done'
        assert await lease.claim_next(conn, run_id) is None
    finally:
        await conn.close()


async def test_claim_next_claims_task_once_dependency_done(postgres_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=postgres_dsn)
    try:
        run_id = await insert_run(conn)
        await insert_task(conn, run_id, "upstream")
        await insert_task(conn, run_id, "downstream", depends_on=["upstream"])

        upstream = await lease.claim_next(conn, run_id)
        assert upstream is not None
        assert await lease.complete(
            conn, upstream.task_id, upstream.lease_token, cost_usd=0, latency_ms=1
        )

        downstream = await lease.claim_next(conn, run_id)
        assert downstream is not None
        assert downstream.node_key == "downstream"
    finally:
        await conn.close()


async def test_renew_extends_lease_while_token_matches(postgres_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=postgres_dsn)
    try:
        run_id = await insert_run(conn)
        await insert_task(conn, run_id, "t")
        claimed = await lease.claim_next(conn, run_id, lease_duration=timedelta(milliseconds=50))
        assert claimed is not None
        assert await lease.renew(
            conn, claimed.task_id, claimed.lease_token, lease_duration=timedelta(seconds=5)
        )
    finally:
        await conn.close()


async def test_renew_fails_with_wrong_token(postgres_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=postgres_dsn)
    try:
        run_id = await insert_run(conn)
        await insert_task(conn, run_id, "t")
        claimed = await lease.claim_next(conn, run_id)
        assert claimed is not None
        import uuid

        assert not await lease.renew(conn, claimed.task_id, uuid.uuid4())
    finally:
        await conn.close()


async def test_zombie_write_rejected_after_a_second_worker_completes(postgres_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=postgres_dsn)
    try:
        run_id = await insert_run(conn)
        task_id = await insert_task(conn, run_id, "t")

        worker_a = await lease.claim_next(conn, run_id, lease_duration=timedelta(milliseconds=1))
        assert worker_a is not None

        # Force the lease into the past and sweep, simulating real expiry.
        await conn.execute(
            "UPDATE tasks SET lease_expires_at = now() - interval '1 second' WHERE id = $1",
            task_id,
        )
        # sweep_expired is intentionally global (any expired lease, any run),
        # so other tests' leftover rows may also be swept here — only assert
        # on this test's own task.
        swept = await lease.sweep_expired(conn)
        assert swept >= 1
        assert await task_status(conn, task_id) == "pending"

        worker_b = await lease.claim_next(conn, run_id)
        assert worker_b is not None
        assert worker_b.task_id == task_id
        assert worker_b.lease_token != worker_a.lease_token

        # The rightful (second) worker completes normally.
        assert await lease.complete(
            conn, worker_b.task_id, worker_b.lease_token, cost_usd=0.01, latency_ms=5
        )
        assert await task_status(conn, task_id) == "done"

        # Guard 1: the zombie's late completion, with its stale token, must
        # affect zero rows and must not resurrect or overwrite the task.
        zombie_wrote = await lease.complete(
            conn, worker_a.task_id, worker_a.lease_token, cost_usd=999, latency_ms=999
        )
        assert zombie_wrote is False
        assert await task_status(conn, task_id) == "done"
        cost = await conn.fetchval("SELECT cost_usd FROM tasks WHERE id = $1", task_id)
        assert float(cost) == 0.01
    finally:
        await conn.close()


async def test_fail_requeues_retryable_below_max_attempts(postgres_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=postgres_dsn)
    try:
        run_id = await insert_run(conn)
        await insert_task(conn, run_id, "t")
        claimed = await lease.claim_next(conn, run_id)
        assert claimed is not None
        ok = await lease.fail(
            conn,
            claimed.task_id,
            claimed.lease_token,
            error="503",
            retryable=True,
            attempt=1,
            max_attempts=3,
        )
        assert ok
        assert await task_status(conn, claimed.task_id) == "pending"
    finally:
        await conn.close()


async def test_fail_is_terminal_after_max_attempts(postgres_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=postgres_dsn)
    try:
        run_id = await insert_run(conn)
        await insert_task(conn, run_id, "t")
        claimed = await lease.claim_next(conn, run_id)
        assert claimed is not None
        ok = await lease.fail(
            conn,
            claimed.task_id,
            claimed.lease_token,
            error="503",
            retryable=True,
            attempt=3,
            max_attempts=3,
        )
        assert ok
        assert await task_status(conn, claimed.task_id) == "failed"
    finally:
        await conn.close()


async def test_fail_guard1_rejects_stale_token(postgres_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=postgres_dsn)
    try:
        run_id = await insert_run(conn)
        await insert_task(conn, run_id, "t")
        claimed = await lease.claim_next(conn, run_id)
        assert claimed is not None
        import uuid

        ok = await lease.fail(
            conn,
            claimed.task_id,
            uuid.uuid4(),
            error="x",
            retryable=False,
            attempt=1,
            max_attempts=3,
        )
        assert ok is False
    finally:
        await conn.close()


async def test_skip_claimed_releases_a_running_task(postgres_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=postgres_dsn)
    try:
        run_id = await insert_run(conn)
        await insert_task(conn, run_id, "t")
        claimed = await lease.claim_next(conn, run_id)
        assert claimed is not None
        assert await lease.skip_claimed(
            conn, claimed.task_id, claimed.lease_token, reason="budget_weight"
        )
        assert await task_status(conn, claimed.task_id) == "skipped"
    finally:
        await conn.close()


async def test_skip_unreachable_marks_dead_branches(postgres_dsn: str) -> None:
    conn = await asyncpg.connect(dsn=postgres_dsn)
    try:
        run_id = await insert_run(conn)
        failed_id = await insert_task(conn, run_id, "upstream")
        blocked_id = await insert_task(conn, run_id, "downstream", depends_on=["upstream"])

        claimed = await lease.claim_next(conn, run_id)
        assert claimed is not None
        assert await lease.fail(
            conn,
            claimed.task_id,
            claimed.lease_token,
            error="x",
            retryable=False,
            attempt=1,
            max_attempts=3,
        )
        assert await task_status(conn, failed_id) == "failed"

        # downstream can never become claimable now
        assert await lease.claim_next(conn, run_id) is None
        n = await lease.skip_unreachable(conn, run_id)
        assert n == 1
        assert await task_status(conn, blocked_id) == "skipped"
    finally:
        await conn.close()
