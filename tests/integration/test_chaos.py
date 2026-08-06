"""The phase's real deliverable (see docs/execution_phases/phase-02-executor-core.md):
concurrency and crash-recovery scenarios that are nearly impossible to hit by
accident in normal testing, so they stay broken silently without a
deliberate, deterministic reproduction.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import asyncpg
import pytest
from _db import insert_run, insert_task, task_status
from _synthetic import AlwaysFailTask, SleepTask, SleepTaskShortTimeout, SpawnTask

from api.executor import (
    ExecutionPlan,
    Executor,
    ExecutorEvent,
    HandlerRegistry,
    RunFinished,
    TaskSpec,
    lease,
)

pytestmark = pytest.mark.usefixtures("skip_without_postgres")

WORKER_SCRIPT = str(Path(__file__).parent / "_worker_process.py")


async def _drain(events: AsyncIterator[ExecutorEvent]) -> list[ExecutorEvent]:
    return [event async for event in events]


async def test_worker_killed_mid_task_recovers_via_sweep(
    pg_pool: asyncpg.Pool, postgres_dsn: str
) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        task_id = await insert_task(conn, run_id, "t", kind="sleep_task", args={"ms": 600})

    proc = subprocess.Popen([sys.executable, WORKER_SCRIPT, postgres_dsn, run_id, "0.2"])
    try:
        for _ in range(100):
            async with pg_pool.acquire() as conn:
                status = await task_status(conn, task_id)
            if status == "running":
                break
            await asyncio.sleep(0.02)
        assert status == "running", "subprocess never claimed the task in time"

        proc.kill()
        proc.wait(timeout=5)

        # past the subprocess's 0.2s lease so the row is recoverable
        await asyncio.sleep(0.3)

        # The recovery worker's own lease just needs to comfortably outlast
        # the task's remaining work (600ms) — only the *killed* subprocess's
        # lease needed to be short, so sweep would recover it quickly.
        registry = HandlerRegistry()
        registry.register(SleepTask())
        executor = Executor(
            pg_pool, registry, lease_duration=timedelta(seconds=5), empty_backoff_s=0.02
        )
        await asyncio.wait_for(
            _drain(executor.submit(run_id, ExecutionPlan(tasks=[]), budget_weight=100)),
            timeout=5,
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    async with pg_pool.acquire() as conn:
        final_status = await task_status(conn, task_id)
        attempts = await conn.fetchval("SELECT attempts FROM tasks WHERE id = $1", task_id)
    assert final_status == "done"
    assert attempts == 2  # claimed once by the killed process, once by recovery


async def test_retry_storm_is_bounded(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)

    plan = ExecutionPlan(
        tasks=[
            TaskSpec(node_key=f"t{i}", kind="always_fail_task", args={"code": 429})
            for i in range(50)
        ]
    )
    registry = HandlerRegistry()
    registry.register(AlwaysFailTask())
    executor = Executor(
        pg_pool, registry, empty_backoff_s=0.01, retry_base_s=0.01, retry_cap_s=0.05
    )
    events = await asyncio.wait_for(
        _drain(executor.submit(run_id, plan, budget_weight=1000)), timeout=15
    )

    async with pg_pool.acquire() as conn:
        rows = await conn.fetch("SELECT status, attempts FROM tasks WHERE run_id = $1", run_id)
    assert len(rows) == 50
    assert all(r["status"] == "failed" for r in rows)
    assert all(r["attempts"] == 3 for r in rows)  # each stops at MAX_ATTEMPTS
    assert sum(r["attempts"] for r in rows) <= 150  # 50 tasks * 3 attempts

    finished = next(e for e in events if isinstance(e, RunFinished))
    assert finished.failed == 50
    assert finished.coverage == 0.0


async def test_runaway_fanout_halted_by_budget(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)

    plan = ExecutionPlan(
        tasks=[
            TaskSpec(
                node_key="root",
                kind="spawn_task",
                args={"n": 5, "recursive": True},
                budget_weight=1,
            )
        ]
    )
    registry = HandlerRegistry()
    registry.register(SpawnTask())
    executor = Executor(pg_pool, registry, empty_backoff_s=0.005)
    events = await asyncio.wait_for(
        _drain(executor.submit(run_id, plan, budget_weight=20)), timeout=15
    )

    finished = next(e for e in events if isinstance(e, RunFinished))
    assert finished.done == 20  # exactly the weight cap, weight=1 per task
    assert finished.skipped > 0  # the rest of the recursive fan-out was halted

    async with pg_pool.acquire() as conn:
        prog = await lease.progress(conn, run_id)
    assert prog.pending == 0
    assert prog.running == 0


async def test_all_branches_fail_terminates_cleanly(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)

    plan = ExecutionPlan(
        tasks=[
            TaskSpec(node_key="a", kind="always_fail_task", args={"code": 404}),
            TaskSpec(node_key="b", kind="always_fail_task", args={"code": 404}, depends_on=["a"]),
        ]
    )
    registry = HandlerRegistry()
    registry.register(AlwaysFailTask())
    executor = Executor(pg_pool, registry, empty_backoff_s=0.01)
    events = await asyncio.wait_for(
        _drain(executor.submit(run_id, plan, budget_weight=10)), timeout=5
    )

    finished = next(e for e in events if isinstance(e, RunFinished))
    assert finished.done == 0
    assert finished.coverage == 0.0
    assert finished.failed == 1  # only 'a' ever ran (non-retryable, fails immediately)
    assert finished.skipped == 1  # 'b' is a dead branch: its dependency never reached 'done'


async def test_timeout_enforcement_cancels_and_releases_lease(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        task_id = await insert_task(
            conn, run_id, "t", kind="sleep_task_short_timeout", args={"ms": 500}
        )

    registry = HandlerRegistry()
    registry.register(SleepTaskShortTimeout())
    executor = Executor(
        pg_pool, registry, empty_backoff_s=0.01, retry_base_s=0.01, retry_cap_s=0.05
    )
    events = await asyncio.wait_for(
        _drain(executor.submit(run_id, ExecutionPlan(tasks=[]), budget_weight=10)), timeout=10
    )

    async with pg_pool.acquire() as conn:
        final_status = await task_status(conn, task_id)
        lease_token = await conn.fetchval("SELECT lease_token FROM tasks WHERE id = $1", task_id)
        error = await conn.fetchval("SELECT error FROM tasks WHERE id = $1", task_id)
    assert final_status == "failed"
    assert lease_token is None  # lease released, not left dangling
    assert "timed out" in (error or "").lower() or "timeout" in (error or "").lower()

    finished = next(e for e in events if isinstance(e, RunFinished))
    assert finished.failed == 1
