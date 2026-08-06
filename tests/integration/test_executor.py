"""Real-Postgres tests for the full `Executor.submit` dispatch loop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import asyncpg
import pytest
from _db import ensure_side_effects_table, insert_run, insert_task
from _synthetic import CountingTask, EmitEventsTask, SpawnTask

from api.executor import (
    ExecutionPlan,
    Executor,
    ExecutorEvent,
    HandlerRegistry,
    RunFinished,
    TaskCompleted,
    TaskSpec,
    TaskStarted,
    lease,
    store,
)

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


def _registry(pool: asyncpg.Pool) -> HandlerRegistry:
    registry = HandlerRegistry()
    for handler in (CountingTask(pool), EmitEventsTask(), SpawnTask()):
        registry.register(handler)
    return registry


async def _drain(events: AsyncIterator[ExecutorEvent]) -> list[ExecutorEvent]:
    return [event async for event in events]


async def test_linear_dag_executes_in_dependency_order(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        await ensure_side_effects_table(conn)

    plan = ExecutionPlan(
        tasks=[
            TaskSpec(node_key="a", kind="counting_task"),
            TaskSpec(node_key="b", kind="counting_task", depends_on=["a"]),
            TaskSpec(node_key="c", kind="counting_task", depends_on=["b"]),
        ]
    )
    executor = Executor(pg_pool, _registry(pg_pool), empty_backoff_s=0.02)
    events = await _drain(executor.submit(run_id, plan, budget_weight=100))

    started_order = [e.node_key for e in events if isinstance(e, TaskStarted)]
    assert started_order == ["a", "b", "c"]
    finished = [e for e in events if isinstance(e, RunFinished)]
    assert len(finished) == 1
    assert finished[0].done == 3
    assert finished[0].coverage == 1.0

    async with pg_pool.acquire() as conn:
        prog = await lease.progress(conn, run_id)
    assert prog.done == 3
    assert prog.pending == prog.running == prog.failed == prog.skipped == 0


async def test_two_workers_never_double_execute(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        await ensure_side_effects_table(conn)

    plan = ExecutionPlan(
        tasks=[TaskSpec(node_key=f"t{i}", kind="counting_task") for i in range(20)]
    )
    registry = _registry(pg_pool)
    worker_a = Executor(pg_pool, registry, empty_backoff_s=0.02)
    worker_b = Executor(pg_pool, registry, empty_backoff_s=0.02)

    await asyncio.gather(
        _drain(worker_a.submit(run_id, plan, budget_weight=100)),
        _drain(worker_b.submit(run_id, plan, budget_weight=100)),
    )

    async with pg_pool.acquire() as conn:
        side_effect_count = await conn.fetchval(
            "SELECT count(*) FROM chaos_side_effects WHERE run_id = $1", run_id
        )
        prog = await lease.progress(conn, run_id)
    assert side_effect_count == 20
    assert prog.done == 20


async def test_skip_locked_contention_each_claimer_gets_a_distinct_task(
    pg_pool: asyncpg.Pool,
) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        for i in range(8):
            await insert_task(conn, run_id, f"t{i}")

    async def claim_one() -> lease.ClaimedTask | None:
        async with pg_pool.acquire() as conn:
            return await lease.claim_next(conn, run_id)

    results = await asyncio.gather(*[claim_one() for _ in range(8)])
    assert all(r is not None for r in results)
    task_ids = {r.task_id for r in results if r is not None}
    assert len(task_ids) == 8


async def test_dynamic_spawn_children_run_in_the_same_submit_call(
    pg_pool: asyncpg.Pool,
) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)

    plan = ExecutionPlan(tasks=[TaskSpec(node_key="root", kind="spawn_task", args={"n": 3})])
    executor = Executor(pg_pool, _registry(pg_pool), empty_backoff_s=0.02)
    events = await _drain(executor.submit(run_id, plan, budget_weight=100))

    completed_keys = {e.node_key for e in events if isinstance(e, TaskCompleted)}
    assert completed_keys == {"root", "root/0/0", "root/0/1", "root/0/2"}

    finished = next(e for e in events if isinstance(e, RunFinished))
    assert finished.done == 4
    assert finished.coverage == 1.0


async def test_event_ordering_and_replay_from_cursor(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)

    plan = ExecutionPlan(tasks=[TaskSpec(node_key="e", kind="emit_events_task", args={"k": 5})])
    executor = Executor(pg_pool, _registry(pg_pool), empty_backoff_s=0.02)
    live_events = await _drain(executor.submit(run_id, plan, budget_weight=100))

    async with pg_pool.acquire() as conn:
        all_replayed = await store.read_events(conn, run_id, since_id=0)

    assert [type(e) for _, e in all_replayed] == [type(e) for e in live_events]

    async with pg_pool.acquire() as conn:
        midpoint_id = all_replayed[2][0]
        resumed = await store.read_events(conn, run_id, since_id=midpoint_id)
    assert [type(e) for _, e in resumed] == [type(e) for e in live_events[3:]]
