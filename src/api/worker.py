"""`python -m api.worker` — the Fly worker machine's entrypoint (Phase 15).

Both Fly machines share one Docker image (Dockerfile) and differ only by
this entrypoint: the API machine runs `python -m api.web.main`, the worker
machine runs this module. The worker is deliberately a *second process over
the same Postgres task table*, not a copy of the pipeline:

- a periodic `lease.sweep_expired()` (masterplan §4.2's crash-recovery
  sweep, run continuously rather than only on worker startup) so a lease
  held by a dead API process is reclaimed;
- the same nightly storage maintenance `api.maintenance.run_maintenance`
  does, as an in-Fly redundancy for the GitHub Actions cron (which remains
  the primary — an ephemeral machine must never be the only place a backup
  or eviction job runs, `docs/runbook.md`);
- a structured `worker.health` log line per sweep with live run/task counts,
  which is the "logs carry run_id/task_id on every line" surface for the
  worker.

**A documented deviation, not a silent one.** Runs still execute *inside*
the API process today: `api.web.runner.run_pipeline` is spawned as a
background task behind `POST /runs`, the single-worker design Phase 02/12
explicitly accepted. The phase doc's premise that "a heavy run cannot
starve the API" is therefore not yet true of the current codebase — this
machine exists and is useful (recovery sweep + maintenance + health
observability), but handing task execution off to it is real Phase 02/10
architecture work, logged in `docs/tracker.md`, not built here.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from typing import Any

import asyncpg
import structlog

from api.config import Settings
from api.db import create_pool
from api.executor import lease
from api.maintenance import run_maintenance

logger = structlog.get_logger(__name__)

#: How often the sweep + health snapshot runs.
SWEEP_INTERVAL_S = 60
#: How often the in-Fly maintenance pass runs (the GitHub Actions cron is the
#: primary schedule; this is redundancy for a Fly-side eviction/pin/prune).
MAINTENANCE_INTERVAL_S = 12 * 60 * 60


async def health_snapshot(pool: asyncpg.Pool) -> dict[str, Any]:
    """Live run/task counts for the `worker.health` log line."""
    run_rows = await pool.fetch("SELECT status, count(*) AS n FROM runs GROUP BY status")
    pending_tasks = await pool.fetchval("SELECT count(*) FROM tasks WHERE status = 'pending'")
    return {
        "runs_by_status": {row["status"]: int(row["n"]) for row in run_rows},
        "pending_tasks": int(pending_tasks or 0),
    }


async def run_once(pool: asyncpg.Pool, *, run_maintenance_pass: bool) -> dict[str, Any]:
    """One worker iteration: reclaim expired leases, optionally run the
    maintenance pass, and log a health snapshot. Testable in isolation."""
    async with pool.acquire() as conn:
        swept = await lease.sweep_expired(conn)
    maintenance: dict[str, Any] = {}
    if run_maintenance_pass:
        report = await run_maintenance(pool)
        maintenance = {
            "evicted_sources": report.evicted_sources,
            "pruned_events": report.pruned_events,
            "pinned_sources": report.pinned_sources,
            "db_size_bytes": report.db_size_bytes,
        }
    snapshot = await health_snapshot(pool)
    line = {
        "swept_expired_leases": swept,
        "maintenance_pass": run_maintenance_pass,
        **maintenance,
        **snapshot,
    }
    logger.info("worker.health", **line)
    return line


async def _loop(pool: asyncpg.Pool) -> None:
    last_maintenance = time.monotonic()
    while True:
        elapsed = time.monotonic() - last_maintenance
        if elapsed >= MAINTENANCE_INTERVAL_S:
            last_maintenance = time.monotonic()
            await run_once(pool, run_maintenance_pass=True)
        else:
            await run_once(pool, run_maintenance_pass=False)
        await asyncio.sleep(SWEEP_INTERVAL_S)


async def _amain() -> None:
    settings = Settings()  # type: ignore[call-arg]
    pool = await create_pool(settings)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _request_stop(*_: object) -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # pragma: no cover - signal quirks
            loop.add_signal_handler(sig, _request_stop)

    logger.info("worker.started", sweep_interval_s=SWEEP_INTERVAL_S)
    try:
        task = asyncio.create_task(_loop(pool))
        await stop.wait()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        await pool.close()
    logger.info("worker.stopped")


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
