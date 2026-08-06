"""Standalone worker process for the 'worker killed mid-task' chaos test.

Not collected by pytest (leading underscore). Launched via `subprocess.Popen`
and SIGKILLed mid-execution by the test, so it must be a real OS process, not
an in-process asyncio task — that's the whole point of the scenario.

Usage: python _worker_process.py <dsn> <run_id> [lease_seconds]

Drives an already-populated run (tasks inserted by the test beforehand) to
completion using the standard synthetic-handler registry, then exits 0.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncpg  # noqa: E402
from _synthetic import (  # noqa: E402
    AlwaysFailTask,
    CountingTask,
    EmitEventsTask,
    FailNTimesTask,
    HangForeverTask,
    SleepTask,
    SleepTaskShortTimeout,
    SpawnTask,
)

from api.executor import ExecutionPlan, Executor, HandlerRegistry  # noqa: E402
from api.executor.lease import DEFAULT_LEASE_DURATION  # noqa: E402


async def main() -> None:
    dsn, run_id = sys.argv[1], sys.argv[2]
    lease_duration = (
        timedelta(seconds=float(sys.argv[3])) if len(sys.argv) > 3 else DEFAULT_LEASE_DURATION
    )

    pool = await asyncpg.create_pool(dsn=dsn)
    try:
        registry = HandlerRegistry()
        for handler in (
            SleepTask(),
            SleepTaskShortTimeout(),
            FailNTimesTask(),
            AlwaysFailTask(),
            HangForeverTask(),
            SpawnTask(),
            EmitEventsTask(),
            CountingTask(pool),
        ):
            registry.register(handler)

        executor = Executor(pool, registry, lease_duration=lease_duration, empty_backoff_s=0.05)
        async for _event in executor.submit(run_id, ExecutionPlan(tasks=[]), budget_weight=10_000):
            pass
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
