from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest

pytestmark = pytest.mark.usefixtures("skip_without_postgres")

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_env(dsn: str) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": dsn,
        "OPENROUTER_API_KEY": "test",
        "EXA_API_KEY": "test",
        "GITHUB_TOKEN": "test",
    }


def _alembic(*args: str, dsn: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        check=True,
        cwd=REPO_ROOT,
        env=_alembic_env(dsn),
    )


@pytest.fixture
def clean_dsn(postgres_dsn: str) -> str:
    return postgres_dsn


def test_upgrade_downgrade_upgrade_is_idempotent(clean_dsn: str) -> None:
    _alembic("downgrade", "base", dsn=clean_dsn)
    _alembic("upgrade", "head", dsn=clean_dsn)
    _alembic("downgrade", "base", dsn=clean_dsn)
    _alembic("upgrade", "head", dsn=clean_dsn)


async def test_findings_must_cite_rejects_empty_claim_ids(clean_dsn: str) -> None:
    _alembic("upgrade", "head", dsn=clean_dsn)
    conn = await asyncpg.connect(dsn=clean_dsn)
    try:
        run_id = await _insert_run(conn)
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO findings (run_id, kind, statement, claim_ids) "
                "VALUES ($1, 'pain_point', 'x', ARRAY[]::bigint[])",
                run_id,
            )
    finally:
        await conn.close()


async def test_claims_span_valid_rejects_bad_span(clean_dsn: str) -> None:
    _alembic("upgrade", "head", dsn=clean_dsn)
    conn = await asyncpg.connect(dsn=clean_dsn)
    try:
        run_id = await _insert_run(conn)
        entity_id = await _insert_entity(conn)
        source_id = await _insert_source(conn)
        with pytest.raises(asyncpg.CheckViolationError):
            await _insert_claim(
                conn,
                run_id=run_id,
                entity_id=entity_id,
                source_id=source_id,
                char_start=10,
                char_end=5,
            )
    finally:
        await conn.close()


async def test_claims_unique_span_swallowed_by_on_conflict(clean_dsn: str) -> None:
    _alembic("upgrade", "head", dsn=clean_dsn)
    conn = await asyncpg.connect(dsn=clean_dsn)
    try:
        run_id = await _insert_run(conn)
        entity_id = await _insert_entity(conn)
        source_id = await _insert_source(conn)
        await _insert_claim(
            conn, run_id=run_id, entity_id=entity_id, source_id=source_id, char_start=0, char_end=5
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert_claim(
                conn,
                run_id=run_id,
                entity_id=entity_id,
                source_id=source_id,
                char_start=0,
                char_end=5,
            )

        # ON CONFLICT DO NOTHING is the idempotency guard the Phase 02 executor relies on.
        row_count_before = await conn.fetchval("SELECT count(*) FROM claims")
        await conn.execute(
            "INSERT INTO claims "
            "(run_id, entity_id, attribute, source_id, quote, char_start, char_end, "
            " quote_context, context_offset, grade, extractor_version, confidence) "
            "VALUES ($1, $2, 'pricing.entry_usd_month', $3, 'q', 0, 5, 'q', 0, 'A', 'v1', 0.9) "
            "ON CONFLICT DO NOTHING",
            run_id,
            entity_id,
            source_id,
        )
        row_count_after = await conn.fetchval("SELECT count(*) FROM claims")
        assert row_count_after == row_count_before
    finally:
        await conn.close()


async def _insert_run(conn: asyncpg.Connection) -> str:
    unique = uuid.uuid4().hex
    user_id = await conn.fetchval(
        "INSERT INTO auth.users (email) VALUES ($1) RETURNING id", f"{unique}@example.com"
    )
    run_id = f"r_test_{unique}"
    await conn.execute(
        "INSERT INTO runs (id, user_id, query) VALUES ($1, $2, 'test query')", run_id, user_id
    )
    return run_id


async def _insert_entity(conn: asyncpg.Connection) -> int:
    return await conn.fetchval(  # type: ignore[no-any-return]
        "INSERT INTO entities (entity_key, display_name) VALUES ($1, 'Test') RETURNING id",
        f"web:test-{uuid.uuid4().hex}.example.com",
    )


async def _insert_source(conn: asyncpg.Connection) -> int:
    return await conn.fetchval(  # type: ignore[no-any-return]
        "INSERT INTO sources (canonical_url) VALUES ($1) RETURNING id",
        f"https://example.com/{uuid.uuid4().hex}",
    )


async def _insert_claim(
    conn: asyncpg.Connection,
    *,
    run_id: str,
    entity_id: int,
    source_id: int,
    char_start: int,
    char_end: int,
) -> None:
    await conn.execute(
        "INSERT INTO claims "
        "(run_id, entity_id, attribute, source_id, quote, char_start, char_end, "
        " quote_context, context_offset, grade, extractor_version, confidence) "
        "VALUES ($1, $2, 'pricing.entry_usd_month', $3, 'q', $4, $5, 'q', 0, 'A', 'v1', 0.9)",
        run_id,
        entity_id,
        source_id,
        char_start,
        char_end,
    )
