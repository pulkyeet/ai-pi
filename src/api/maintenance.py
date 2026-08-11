"""Nightly storage maintenance (Phase 15): evict, prune, pin, size.

The 500 MB Supabase ceiling needs active management (phase doc §"Storage
management"). `run_maintenance` is the one routine that does all of it, and
the `python -m api.maintenance` entrypoint lets the keepalive/backup GitHub
Actions cron run it on schedule against the real database (see
`.github/workflows/keepalive.yml` and `docs/runbook.md`):

- **Evict** — `api.retrieval.cache.evict_expired` nulls `extracted_text` on
  expired, unpinned source rows, keeping the metadata row and
  `claims.quote_context` so drill-down still works after eviction (Phase 00).
- **Prune** — `run_events` past the retention window. `run_events` only backs
  SSE replay and executor event streams (Phase 02/12); a run's report is
  persisted in `reports`, so old events are disposable.
- **Pin** — benchmark-run sources are exempt from eviction
  (`sources.is_pinned`, Phase 00's delta 3). A benchmark source that was
  evicted would silently break cached-only CI replay and the public
  homepage's drill-down, so `pin_benchmark_sources` re-applies the flag to
  every source a benchmark run's claims cite (it must be re-run: pinning is
  a one-way flag set at benchmark time, and the upsert path deliberately
  never overwrites it).
- **Size** — `pg_database_size(current_database())`, the raw number the
  runbook's 70%/85% alerts read.

Asyncpg-only (the app's one runtime DB driver); this module never touches
psycopg/SQLAlchemy — those remain Alembic-only.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass

import asyncpg
import structlog

from api.config import Settings
from api.db import create_pool
from api.retrieval.cache import evict_expired as _evict_expired_sources

logger = structlog.get_logger(__name__)

#: run_events retention window (days). Events only serve SSE replay / the
#: executor's cursor stream for an in-progress or recently-finished run;
#: 30 days is generous against any reconnect/audit need.
EVENT_RETENTION_DAYS = 30


@dataclass(frozen=True)
class MaintenanceReport:
    evicted_sources: int
    pruned_events: int
    pinned_sources: int
    db_size_bytes: int


async def prune_expired_events(
    pool: asyncpg.Pool, *, retention_days: int = EVENT_RETENTION_DAYS
) -> int:
    result = await pool.execute(
        "DELETE FROM run_events WHERE created_at < now() - ($1::int * interval '1 day')",
        retention_days,
    )
    return int(result.split()[-1])


async def pin_benchmark_sources(pool: asyncpg.Pool) -> int:
    """Pin every source cited by any `is_benchmark` run's claims. Idempotent
    and cheap to re-run (the WHERE excludes already-pinned rows)."""
    result = await pool.execute(
        """
        UPDATE sources SET is_pinned = true
         WHERE is_pinned = false
           AND id IN (
               SELECT DISTINCT c.source_id
                 FROM claims c
                 JOIN runs r ON r.id = c.run_id
                WHERE r.is_benchmark = true
           )
        """
    )
    return int(result.split()[-1])


async def database_size_bytes(pool: asyncpg.Pool) -> int:
    row = await pool.fetchrow("SELECT pg_database_size(current_database()) AS bytes")
    assert row is not None
    return int(row["bytes"])


async def run_maintenance(
    pool: asyncpg.Pool, *, event_retention_days: int = EVENT_RETENTION_DAYS
) -> MaintenanceReport:
    """Run the full nightly maintenance pass, returning a report a caller
    can log or persist. Eviction and pruning are idempotent; pinning is a
    re-apply so it is safe to run this every night."""
    evicted = await _evict_expired_sources(pool)
    pruned = await prune_expired_events(pool, retention_days=event_retention_days)
    pinned = await pin_benchmark_sources(pool)
    size = await database_size_bytes(pool)
    report = MaintenanceReport(
        evicted_sources=evicted,
        pruned_events=pruned,
        pinned_sources=pinned,
        db_size_bytes=size,
    )
    logger.info(
        "maintenance.done",
        **asdict(report),
    )
    return report


async def _amain() -> None:
    settings = Settings()  # type: ignore[call-arg]
    pool = await create_pool(settings)
    try:
        report = await run_maintenance(pool)
        print(json.dumps(asdict(report), sort_keys=True))
    finally:
        await pool.close()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
