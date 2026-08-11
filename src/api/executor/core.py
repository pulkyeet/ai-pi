"""The executor: `Executor.submit(run_id, plan) -> AsyncIterator[ExecutorEvent]`.

The only public entry point, by design (see
docs/execution_phases/phase-02-executor-core.md) — a boundary narrow enough
to test exhaustively and swap later. Everything else in this package is
reached only through it: task claiming (`lease`), budget admission
(`budget`), retry/backoff (`retry`), and task/event persistence (`store`).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from datetime import timedelta

import asyncpg

from api.executor import lease, retry, store
from api.executor.budget import BudgetTracker
from api.executor.protocol import (
    ExecutionPlan,
    ExecutorEvent,
    HandlerRegistry,
    RunFinished,
    TaskCompleted,
    TaskContext,
    TaskFailed,
    TaskSkipped,
    TaskStarted,
)

DEFAULT_CONCURRENCY: dict[str, int] = {"search": 4, "crawl": 8, "llm": 6, "none": 1000}


class Executor:
    def __init__(
        self,
        pool: asyncpg.Pool,
        registry: HandlerRegistry,
        *,
        concurrency: dict[str, int] | None = None,
        lease_duration: timedelta = lease.DEFAULT_LEASE_DURATION,
        empty_backoff_s: float = 0.5,
        max_attempts: int = retry.MAX_ATTEMPTS,
        retry_base_s: float = retry.DEFAULT_BASE_S,
        retry_cap_s: float = retry.DEFAULT_CAP_S,
    ) -> None:
        self._pool = pool
        self._registry = registry
        self._concurrency = {**DEFAULT_CONCURRENCY, **(concurrency or {})}
        self._lease_duration = lease_duration
        self._empty_backoff_s = empty_backoff_s
        self._max_attempts = max_attempts
        self._retry_base_s = retry_base_s
        self._retry_cap_s = retry_cap_s

    async def submit(
        self,
        run_id: str,
        plan: ExecutionPlan,
        *,
        budget_weight: int,
        budget_usd: float | None = None,
        run_timeout_s: float | None = None,
    ) -> AsyncIterator[ExecutorEvent]:
        """Insert `plan`'s tasks (idempotent — `ON CONFLICT (run_id, node_key)
        DO NOTHING`, so calling this again for the same run_id, e.g. as a
        second worker or a recovered one, does not duplicate tasks) and drive
        the run to completion, yielding events as they happen. Terminates
        after a `run.finished` event.

        `run_timeout_s` (Phase 15 — `Settings.run_timeout_s`, masterplan
        §8.2) stops the run from claiming *new* work once the deadline
        passes: every still-`pending`/`running` task is skipped with reason
        `run_timeout` and the run finishes with whatever already completed.
        In-flight tasks are allowed to finish (their cost is already spent)
        and the report path downstream of the executor still runs, so the
        semantic is "stop the fan-out, finish with what we have" — not an
        abrupt process kill. `None` means no wall-clock cap.
        """
        async with self._pool.acquire() as conn:
            await store.insert_tasks(conn, run_id, plan)

        events: asyncio.Queue[ExecutorEvent] = asyncio.Queue()
        budget_tracker = BudgetTracker(budget_weight, budget_usd)
        driver = asyncio.create_task(
            self._drive(run_id, budget_tracker, events, run_timeout_s=run_timeout_s)
        )
        try:
            while True:
                event = await events.get()
                yield event
                if isinstance(event, RunFinished):
                    break
        finally:
            if not driver.done():
                driver.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await driver

    async def _drive(
        self,
        run_id: str,
        budget: BudgetTracker,
        events: asyncio.Queue[ExecutorEvent],
        *,
        run_timeout_s: float | None = None,
    ) -> None:
        deadline = time.monotonic() + run_timeout_s if run_timeout_s is not None else None
        semaphores = {name: asyncio.Semaphore(n) for name, n in self._concurrency.items()}
        try:
            async with asyncio.TaskGroup() as tg:
                while True:
                    if deadline is not None and time.monotonic() >= deadline:
                        async with self._pool.acquire() as conn:
                            await lease.skip_rest(conn, run_id, reason="run_timeout")
                        break
                    async with self._pool.acquire() as conn:
                        await lease.sweep_expired(conn)
                        claimed = await lease.claim_next(
                            conn, run_id, lease_duration=self._lease_duration
                        )

                    if claimed is None:
                        async with self._pool.acquire() as conn:
                            # Race-free: only touches tasks with a dead
                            # (failed/skipped) dependency, never one merely
                            # waiting out its own retry backoff.
                            await lease.skip_unreachable(conn, run_id)
                            prog = await lease.progress(conn, run_id)
                        if prog.running == 0 and prog.pending == 0:
                            break
                        await asyncio.sleep(self._empty_backoff_s)
                        continue

                    decision = budget.try_reserve(claimed.budget_weight)
                    if not decision.admit:
                        reason = decision.reason or "budget"
                        async with self._pool.acquire() as conn:
                            await lease.skip_claimed(
                                conn, claimed.task_id, claimed.lease_token, reason=reason
                            )
                        await self._emit(
                            events,
                            run_id,
                            TaskSkipped(
                                run_id=run_id,
                                task_id=claimed.task_id,
                                node_key=claimed.node_key,
                                kind=claimed.kind,
                                reason=reason,
                            ),
                        )
                        continue

                    tg.create_task(self._execute(run_id, claimed, semaphores, budget, events))
        finally:
            async with self._pool.acquire() as conn:
                prog = await lease.progress(conn, run_id)
            total_terminal = prog.done + prog.failed + prog.skipped
            coverage = (prog.done / total_terminal) if total_terminal else 0.0
            await self._emit(
                events,
                run_id,
                RunFinished(
                    run_id=run_id,
                    done=prog.done,
                    failed=prog.failed,
                    skipped=prog.skipped,
                    coverage=coverage,
                ),
            )

    async def _execute(
        self,
        run_id: str,
        claimed: lease.ClaimedTask,
        semaphores: dict[str, asyncio.Semaphore],
        budget: BudgetTracker,
        events: asyncio.Queue[ExecutorEvent],
    ) -> None:
        handler = self._registry.get(claimed.kind)
        await self._emit(
            events,
            run_id,
            TaskStarted(
                run_id=run_id, task_id=claimed.task_id, node_key=claimed.node_key, kind=claimed.kind
            ),
        )

        async def renew_lease() -> bool:
            async with self._pool.acquire() as conn:
                return await lease.renew(
                    conn, claimed.task_id, claimed.lease_token, lease_duration=self._lease_duration
                )

        async def emit(event: ExecutorEvent) -> None:
            await self._emit(events, run_id, event)

        ctx = TaskContext(
            run_id=run_id,
            task_id=claimed.task_id,
            node_key=claimed.node_key,
            kind=claimed.kind,
            lease_token=claimed.lease_token,
            attempt=claimed.attempt,
            emit=emit,
            renew_lease=renew_lease,
        )

        sem = semaphores.get(handler.service, semaphores["none"])
        start = time.monotonic()
        async with sem:
            try:
                async with asyncio.timeout(handler.timeout_s):
                    result = await handler.run(ctx, claimed.args)
            except TimeoutError:
                await self._on_failure(
                    run_id,
                    claimed,
                    TimeoutError(f"task timed out after timeout_s={handler.timeout_s}"),
                    events,
                )
                return
            except Exception as exc:  # noqa: BLE001 - classified by retry.is_retryable
                await self._on_failure(run_id, claimed, exc, events)
                return

        latency_ms = int((time.monotonic() - start) * 1000)
        async with self._pool.acquire() as conn:
            ok = await lease.complete(
                conn,
                claimed.task_id,
                claimed.lease_token,
                cost_usd=result.cost_usd,
                latency_ms=latency_ms,
            )
        if not ok:
            # Guard 1: lease was lost mid-execution. Discard the work entirely
            # rather than let a superseded worker's result land.
            return

        budget.record_spend_usd(result.cost_usd)
        await self._emit(
            events,
            run_id,
            TaskCompleted(
                run_id=run_id,
                task_id=claimed.task_id,
                node_key=claimed.node_key,
                kind=claimed.kind,
                cost_usd=result.cost_usd,
                latency_ms=latency_ms,
            ),
        )
        if result.spawned:
            async with self._pool.acquire() as conn:
                await store.insert_spawned(conn, run_id, result.spawned)

    async def _on_failure(
        self,
        run_id: str,
        claimed: lease.ClaimedTask,
        exc: Exception,
        events: asyncio.Queue[ExecutorEvent],
    ) -> None:
        retryable = retry.is_retryable(exc)
        will_retry = retryable and claimed.attempt < self._max_attempts
        delay = (
            timedelta(
                seconds=retry.backoff_delay(
                    claimed.attempt, base=self._retry_base_s, cap=self._retry_cap_s
                )
            )
            if will_retry
            else None
        )
        async with self._pool.acquire() as conn:
            ok = await lease.fail(
                conn,
                claimed.task_id,
                claimed.lease_token,
                error=str(exc),
                retryable=retryable,
                attempt=claimed.attempt,
                max_attempts=self._max_attempts,
                retry_delay=delay,
            )
        if not ok:
            # Guard 1: lease already lost; someone else owns this task now.
            return
        if not will_retry:
            await self._emit(
                events,
                run_id,
                TaskFailed(
                    run_id=run_id,
                    task_id=claimed.task_id,
                    node_key=claimed.node_key,
                    kind=claimed.kind,
                    error=str(exc),
                    attempts=claimed.attempt,
                ),
            )

    async def _emit(
        self, events: asyncio.Queue[ExecutorEvent], run_id: str, event: ExecutorEvent
    ) -> None:
        async with self._pool.acquire() as conn:
            await store.persist_event(conn, run_id, event)
        await events.put(event)
