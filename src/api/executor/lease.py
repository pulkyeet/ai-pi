"""Postgres-backed leasing: claim, renew, complete, fail, sweep.

Masterplan §4.2's `SELECT ... FOR UPDATE SKIP LOCKED` verbatim, extended with
two things the reference pseudocode leaves implicit (see
docs/execution_phases/phase-02-executor-core.md):

  - a dependency-met filter, so a task with unsatisfied `depends_on` is never
    claimed;
  - `lease_expires_at` doing double duty as "earliest retry time" while a task
    is `pending` (set by `fail()` on a retryable failure) as well as its usual
    meaning of "lease deadline" while `running` — avoids a second column for
    what is, in both cases, "don't touch this row before this timestamp".

Guard 1 (idempotency) is `lease_token` equality on every terminal write
(`complete`, `fail`) — a worker that lost its lease affects zero rows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

import asyncpg

DEFAULT_LEASE_DURATION = timedelta(minutes=2)


@dataclass
class ClaimedTask:
    task_id: int
    node_key: str
    kind: str
    args: dict[str, Any]
    budget_weight: int
    lease_token: UUID
    attempt: int


@dataclass
class RunProgress:
    pending: int
    running: int
    done: int
    failed: int
    skipped: int


async def claim_next(
    conn: asyncpg.Connection,
    run_id: str,
    *,
    lease_duration: timedelta = DEFAULT_LEASE_DURATION,
) -> ClaimedTask | None:
    row = await conn.fetchrow(
        """
        WITH candidate AS (
            SELECT t.id
              FROM tasks t
             WHERE t.run_id = $1
               AND t.status = 'pending'
               AND (t.lease_expires_at IS NULL OR t.lease_expires_at <= now())
               AND NOT EXISTS (
                   SELECT 1
                     FROM unnest(t.depends_on) AS dep(node_key)
                     LEFT JOIN tasks dt
                       ON dt.run_id = t.run_id AND dt.node_key = dep.node_key
                    WHERE dt.id IS NULL OR dt.status <> 'done'
               )
             ORDER BY t.priority
             LIMIT 1
             FOR UPDATE SKIP LOCKED
        )
        UPDATE tasks
           SET status = 'running',
               lease_token = gen_random_uuid(),
               lease_expires_at = now() + $2::interval,
               attempts = attempts + 1
         WHERE id = (SELECT id FROM candidate)
        RETURNING id, node_key, kind, args, budget_weight, lease_token, attempts
        """,
        run_id,
        lease_duration,
    )
    if row is None:
        return None
    args = row["args"]
    return ClaimedTask(
        task_id=row["id"],
        node_key=row["node_key"],
        kind=row["kind"],
        args=json.loads(args) if isinstance(args, str) else args,
        budget_weight=row["budget_weight"],
        lease_token=row["lease_token"],
        attempt=row["attempts"],
    )


async def renew(
    conn: asyncpg.Connection,
    task_id: int,
    lease_token: UUID,
    *,
    lease_duration: timedelta = DEFAULT_LEASE_DURATION,
) -> bool:
    result = await conn.execute(
        """
        UPDATE tasks SET lease_expires_at = now() + $3::interval
         WHERE id = $1 AND lease_token = $2 AND status = 'running'
        """,
        task_id,
        lease_token,
        lease_duration,
    )
    return _rowcount(result) == 1


async def complete(
    conn: asyncpg.Connection,
    task_id: int,
    lease_token: UUID,
    *,
    cost_usd: float,
    latency_ms: int,
) -> bool:
    result = await conn.execute(
        """
        UPDATE tasks
           SET status = 'done', lease_token = NULL, lease_expires_at = NULL,
               cost_usd = $3, latency_ms = $4
         WHERE id = $1 AND lease_token = $2 AND status = 'running'
        """,
        task_id,
        lease_token,
        cost_usd,
        latency_ms,
    )
    return _rowcount(result) == 1


async def fail(
    conn: asyncpg.Connection,
    task_id: int,
    lease_token: UUID,
    *,
    error: str,
    retryable: bool,
    attempt: int,
    max_attempts: int,
    retry_delay: timedelta | None = None,
) -> bool:
    if retryable and attempt < max_attempts:
        result = await conn.execute(
            """
            UPDATE tasks
               SET status = 'pending', lease_token = NULL,
                   lease_expires_at = now() + $3::interval, error = $4
             WHERE id = $1 AND lease_token = $2 AND status = 'running'
            """,
            task_id,
            lease_token,
            retry_delay or timedelta(0),
            error,
        )
    else:
        result = await conn.execute(
            """
            UPDATE tasks
               SET status = 'failed', lease_token = NULL, lease_expires_at = NULL, error = $3
             WHERE id = $1 AND lease_token = $2 AND status = 'running'
            """,
            task_id,
            lease_token,
            error,
        )
    return _rowcount(result) == 1


async def skip_claimed(
    conn: asyncpg.Connection, task_id: int, lease_token: UUID, *, reason: str
) -> bool:
    """Release a just-claimed (`running`) task back out as `skipped`, e.g. a
    budget rejection discovered after the atomic claim already flipped it to
    `running`. Guarded by `lease_token`, same as `complete`/`fail`."""
    result = await conn.execute(
        """
        UPDATE tasks
           SET status = 'skipped', lease_token = NULL, lease_expires_at = NULL, error = $3
         WHERE id = $1 AND lease_token = $2 AND status = 'running'
        """,
        task_id,
        lease_token,
        reason,
    )
    return _rowcount(result) == 1


async def skip_unreachable(conn: asyncpg.Connection, run_id: str) -> int:
    """Mark every `pending` task with a dead dependency as `skipped`.

    Precise and race-free by construction: a task only matches if one of its
    `depends_on` node_keys names a task that is `failed`/`skipped` (or
    doesn't exist), i.e. can provably never reach `done`. A task merely
    waiting out its own retry backoff has no such dependency (most have none
    at all) and is never touched, however small the remaining backoff window
    — no need to coordinate this against retry timing. Safe to call on every
    "nothing claimable" tick; it's a no-op once there's nothing dead to find.
    """
    result = await conn.execute(
        """
        UPDATE tasks t
           SET status = 'skipped', error = 'unreachable: dependency did not complete'
         WHERE t.run_id = $1
           AND t.status = 'pending'
           AND EXISTS (
               SELECT 1
                 FROM unnest(t.depends_on) AS dep(node_key)
                 LEFT JOIN tasks dt
                   ON dt.run_id = t.run_id AND dt.node_key = dep.node_key
                WHERE dt.id IS NULL OR dt.status IN ('failed', 'skipped')
           )
        """,
        run_id,
    )
    return _rowcount(result)


async def skip_rest(conn: asyncpg.Connection, run_id: str, *, reason: str) -> int:
    """Mark every not-yet-terminal task of a run as `skipped` (Phase 15's
    `run_timeout_s` enforcement in `Executor._drive`). In-flight `running`
    tasks still attempt their own terminal write under Guard 1 (`lease_token`
    equality), which then affects zero rows — the lease was released here, so
    a superseded worker's result is discarded exactly as in any other
    lost-lease case."""
    result = await conn.execute(
        """
        UPDATE tasks
           SET status = 'skipped', lease_token = NULL, lease_expires_at = NULL, error = $2
         WHERE run_id = $1 AND status IN ('pending', 'running')
        """,
        run_id,
        reason,
    )
    return _rowcount(result)


async def sweep_expired(conn: asyncpg.Connection) -> int:
    result = await conn.execute(
        """
        UPDATE tasks
           SET status = 'pending', lease_token = NULL, lease_expires_at = NULL
         WHERE status = 'running' AND lease_expires_at < now()
        """
    )
    return _rowcount(result)


async def progress(conn: asyncpg.Connection, run_id: str) -> RunProgress:
    rows = await conn.fetch(
        "SELECT status, count(*) AS n FROM tasks WHERE run_id = $1 GROUP BY status", run_id
    )
    counts = {row["status"]: row["n"] for row in rows}
    return RunProgress(
        pending=counts.get("pending", 0),
        running=counts.get("running", 0),
        done=counts.get("done", 0),
        failed=counts.get("failed", 0),
        skipped=counts.get("skipped", 0),
    )


def _rowcount(result: str) -> int:
    # asyncpg command tags look like "UPDATE 1"
    return int(result.rsplit(" ", 1)[-1])
