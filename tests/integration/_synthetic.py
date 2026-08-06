"""Synthetic TaskHandlers, per docs/execution_phases/phase-02-executor-core.md.

Deterministic, in-milliseconds failure modes so the executor's concurrency
and recovery mechanisms can be hardened without any real network I/O.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg

from api.executor.protocol import HandlerResult, SpawnRequest, TaskContext, TaskProgress
from api.executor.retry import NonRetryableError, RetryableError

NON_RETRYABLE_CODES = frozenset({400, 401, 403, 404, 422})


class SleepTask:
    kind = "sleep_task"
    cost_weight = 1
    service = "none"
    timeout_s = 30.0

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        await asyncio.sleep(args["ms"] / 1000)
        return HandlerResult()


class SleepTaskShortTimeout(SleepTask):
    """Same behaviour, short timeout — for the timeout-enforcement scenario."""

    kind = "sleep_task_short_timeout"
    timeout_s = 0.05


class FailNTimesTask:
    """Fails on every attempt where `ctx.attempt <= n`, then succeeds.

    Reuses the DB-tracked `attempts` counter (surfaced as `ctx.attempt`)
    rather than keeping handler-local state, since a handler instance is
    shared across the whole process/registry.
    """

    kind = "fail_n_times_task"
    cost_weight = 1
    service = "none"
    timeout_s = 5.0

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        if ctx.attempt <= args["n"]:
            raise RetryableError("synthetic failure", code=503)
        return HandlerResult()


class AlwaysFailTask:
    kind = "always_fail_task"
    cost_weight = 1
    service = "none"
    timeout_s = 5.0

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        code = args["code"]
        if code in NON_RETRYABLE_CODES:
            raise NonRetryableError("synthetic non-retryable failure", code=code)
        raise RetryableError("synthetic retryable failure", code=code)


class HangForeverTask:
    kind = "hang_forever_task"
    cost_weight = 1
    service = "none"
    timeout_s = 3600.0

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover


class SpawnTask:
    """Enqueues `n` children. Non-recursive by default (children spawn
    nothing further) — pass `recursive: true` for the runaway-fan-out chaos
    scenario, where children keep spawning `n` more of themselves forever.
    """

    kind = "spawn_task"
    cost_weight = 1
    service = "none"
    timeout_s = 5.0

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        n = args.get("n", 0)
        depth = args.get("depth", 0)
        recursive = args.get("recursive", False)
        child_n = n if recursive else 0
        spawned = [
            SpawnRequest(
                node_key=f"{ctx.node_key}/{depth}/{i}",
                kind="spawn_task",
                args={"n": child_n, "depth": depth + 1, "recursive": recursive},
                budget_weight=1,
            )
            for i in range(n)
        ]
        return HandlerResult(spawned=spawned)


class EmitEventsTask:
    kind = "emit_events_task"
    cost_weight = 1
    service = "none"
    timeout_s = 5.0

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        for i in range(args["k"]):
            await ctx.emit(
                TaskProgress(
                    run_id=ctx.run_id,
                    task_id=ctx.task_id,
                    node_key=ctx.node_key,
                    payload={"i": i},
                )
            )
        return HandlerResult()


class CountingTask:
    """Writes one row per real execution into a scratch table: proves
    'no task executes twice' under fan-out/contention, and stands in for
    Guard 2 (the claims table's UNIQUE + ON CONFLICT DO NOTHING) since the
    domain-agnostic executor writes no claims of its own.
    """

    kind = "counting_task"
    cost_weight = 1
    service = "none"
    timeout_s = 5.0

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO chaos_side_effects (run_id, node_key) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                ctx.run_id,
                ctx.node_key,
            )
        return HandlerResult()
