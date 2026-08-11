"""Phase 15: the nightly storage-maintenance pass (eviction, run_events
pruning, benchmark-source pinning, DB size) plus the `run_stats` write that
feeds the metrics endpoint's extraction-drop-rate alert."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from _db import insert_run

from api.cli import record_run_stats
from api.maintenance import (
    EVENT_RETENTION_DAYS,
    MaintenanceReport,
    database_size_bytes,
    pin_benchmark_sources,
    prune_expired_events,
    run_maintenance,
)
from api.tasks.context import RunStats

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


async def _insert_run_with_source(
    conn: asyncpg.Connection, *, is_benchmark: bool = False, pinned: bool = False
) -> tuple[str, int]:
    """A run + a source + one claim citing it. Returns (run_id, source_id)."""
    unique = uuid.uuid4().hex
    user_id = await conn.fetchval(
        "INSERT INTO auth.users (email) VALUES ($1) RETURNING id", f"{unique}@example.com"
    )
    run_id = f"r_maint_{unique}"
    await conn.execute(
        "INSERT INTO runs (id, user_id, query, started_at, is_benchmark) "
        "VALUES ($1, $2, 'maintenance test', now(), $3)",
        run_id,
        user_id,
        is_benchmark,
    )
    canonical = f"https://maint-{unique}.example.com/page"
    source_id = await conn.fetchval(
        "INSERT INTO sources (canonical_url, root_key, extracted_text, ttl_expires_at, is_pinned) "
        "VALUES ($1, $2, 'the exact quote text lives here', now() - interval '1 day', $3) "
        "RETURNING id",
        canonical,
        f"maint-{unique}.example.com",
        pinned,
    )
    entity_id = await conn.fetchval(
        "INSERT INTO entities (entity_key, display_name) VALUES ($1, 'test') RETURNING id",
        f"web:maint-{unique}.example.com",
    )
    assert source_id is not None
    assert entity_id is not None
    await conn.execute(
        """
        INSERT INTO claims (run_id, entity_id, source_id, attribute, quote, char_start,
                            char_end, quote_context, context_offset, grade,
                            extractor_version, confidence)
        VALUES ($1, $2, $3, 'product.launch_date', 'exact quote text', 0, 17, '', 0,
                'A', 'test', 0.5)
        """,
        run_id,
        entity_id,
        source_id,
    )
    return run_id, int(source_id)


async def test_run_maintenance_evicts_expired_and_keeps_row_and_pinned(
    pg_pool: asyncpg.Pool,
) -> None:
    async with pg_pool.acquire() as conn:
        expired_run, expired_source = await _insert_run_with_source(conn)
        pinned_run, pinned_source = await _insert_run_with_source(conn, pinned=True)

    report = await run_maintenance(pg_pool)
    assert isinstance(report, MaintenanceReport)
    assert report.evicted_sources >= 1

    async with pg_pool.acquire() as conn:
        evicted = await conn.fetchrow(
            "SELECT extracted_text FROM sources WHERE id = $1", expired_source
        )
        kept = await conn.fetchrow(
            "SELECT extracted_text FROM sources WHERE id = $1", pinned_source
        )
        row_still_there = await conn.fetchval(
            "SELECT count(*) FROM sources WHERE id = $1", expired_source
        )
    assert evicted is not None and evicted["extracted_text"] is None
    assert kept is not None and kept["extracted_text"] == "the exact quote text lives here"
    assert row_still_there == 1


async def test_prune_expired_events_deletes_only_old_rows(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        old_ts = datetime.now(UTC) - timedelta(days=EVENT_RETENTION_DAYS + 10)
        await conn.execute(
            "INSERT INTO run_events (run_id, event_type, payload, created_at) "
            "VALUES ($1, 'task.completed', '{}'::jsonb, $2)",
            run_id,
            old_ts,
        )
        await conn.execute(
            "INSERT INTO run_events (run_id, event_type, payload) "
            "VALUES ($1, 'task.started', '{}'::jsonb)",
            run_id,
        )

    pruned = await prune_expired_events(pg_pool)
    assert pruned == 1

    async with pg_pool.acquire() as conn:
        remaining = await conn.fetchval(
            "SELECT event_type FROM run_events WHERE run_id = $1", run_id
        )
    assert remaining == "task.started"


async def test_pin_benchmark_sources_is_idempotent(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        bench_run, bench_source = await _insert_run_with_source(conn, is_benchmark=True)
        normal_run, normal_source = await _insert_run_with_source(conn, is_benchmark=False)

    first = await pin_benchmark_sources(pg_pool)
    assert first >= 1
    # Second pass finds nothing left to pin.
    assert await pin_benchmark_sources(pg_pool) == 0

    async with pg_pool.acquire() as conn:
        bench_pinned = await conn.fetchval(
            "SELECT is_pinned FROM sources WHERE id = $1", bench_source
        )
        normal_pinned = await conn.fetchval(
            "SELECT is_pinned FROM sources WHERE id = $1", normal_source
        )
    assert bench_pinned is True
    assert normal_pinned is False


async def test_database_size_bytes_is_positive(pg_pool: asyncpg.Pool) -> None:
    size = await database_size_bytes(pg_pool)
    assert size > 0


async def test_record_run_stats_upserts(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)

    stats = RunStats()
    stats.claims_bound = 7
    stats.claims_dropped = {"quote_not_in_source": 3, "value_type_mismatch": 1}
    await record_run_stats(pg_pool, run_id, stats)
    await record_run_stats(pg_pool, run_id, stats)  # second write upserts, no duplicate

    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT claims_bound, claims_dropped FROM run_stats WHERE run_id = $1", run_id
        )
    assert row is not None
    assert row["claims_bound"] == 7
    payload = (
        json.loads(row["claims_dropped"])
        if isinstance(row["claims_dropped"], str)
        else row["claims_dropped"]
    )
    assert payload == {"quote_not_in_source": 3, "value_type_mismatch": 1}
