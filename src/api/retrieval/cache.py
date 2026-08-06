"""Source cache and path-guess cache: read/write against Postgres.

`sources` is keyed by `canonical_url` (unique). `is_pinned` rows are exempt
from TTL eviction (benchmark sources); eviction nulls `extracted_text` on
expired, unpinned rows while keeping the metadata row, so drill-down can
still fall back to `claims.quote_context` (Phase 00) after eviction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg

from api.models.source import Source

_COLUMNS = (
    "id, canonical_url, root_key, fetched_at, http_status, content_hash, "
    "extracted_text, etag, last_modified, retrieval_reason, ttl_expires_at, is_pinned"
)


def _row_to_source(row: asyncpg.Record) -> Source:
    return Source(**dict(row))


async def get_fresh(pool: asyncpg.Pool, canonical_url: str) -> Source | None:
    """A cached row only counts as a hit if its TTL hasn't lapsed."""
    row = await pool.fetchrow(
        f"SELECT {_COLUMNS} FROM sources "
        "WHERE canonical_url = $1 AND ttl_expires_at IS NOT NULL AND ttl_expires_at > now()",
        canonical_url,
    )
    return _row_to_source(row) if row else None


async def get_any(pool: asyncpg.Pool, canonical_url: str) -> Source | None:
    """Any existing row regardless of freshness — the source of `etag` /
    `last_modified` for a conditional request on refetch."""
    row = await pool.fetchrow(
        f"SELECT {_COLUMNS} FROM sources WHERE canonical_url = $1", canonical_url
    )
    return _row_to_source(row) if row else None


async def upsert(
    pool: asyncpg.Pool,
    *,
    canonical_url: str,
    root_key: str | None,
    http_status: int | None,
    extracted_text: str | None,
    content_hash: str | None,
    etag: str | None,
    last_modified: str | None,
    retrieval_reason: str | None,
    ttl: timedelta,
    is_pinned: bool = False,
) -> Source:
    ttl_expires_at = datetime.now(UTC) + ttl
    row = await pool.fetchrow(
        f"""
        INSERT INTO sources (canonical_url, root_key, fetched_at, http_status, content_hash,
                              extracted_text, etag, last_modified, retrieval_reason,
                              ttl_expires_at, is_pinned)
        VALUES ($1, $2, now(), $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (canonical_url) DO UPDATE SET
            root_key         = EXCLUDED.root_key,
            fetched_at       = EXCLUDED.fetched_at,
            http_status      = EXCLUDED.http_status,
            content_hash     = EXCLUDED.content_hash,
            extracted_text   = EXCLUDED.extracted_text,
            etag             = EXCLUDED.etag,
            last_modified    = EXCLUDED.last_modified,
            retrieval_reason = EXCLUDED.retrieval_reason,
            ttl_expires_at   = EXCLUDED.ttl_expires_at
            -- is_pinned is deliberately not overwritten here: pinning is a
            -- benchmark-time decision that a routine refetch must not clear.
        RETURNING {_COLUMNS}
        """,
        canonical_url,
        root_key,
        http_status,
        content_hash,
        extracted_text,
        etag,
        last_modified,
        retrieval_reason,
        ttl_expires_at,
        is_pinned,
    )
    assert row is not None
    return _row_to_source(row)


async def refresh_ttl(pool: asyncpg.Pool, canonical_url: str, *, ttl: timedelta) -> Source:
    """A 304 response: refresh freshness without touching content or content_hash."""
    row = await pool.fetchrow(
        f"UPDATE sources SET fetched_at = now(), ttl_expires_at = $2 "
        f"WHERE canonical_url = $1 RETURNING {_COLUMNS}",
        canonical_url,
        datetime.now(UTC) + ttl,
    )
    assert row is not None
    return _row_to_source(row)


async def evict_expired(pool: asyncpg.Pool) -> int:
    result = await pool.execute(
        "UPDATE sources SET extracted_text = NULL "
        "WHERE ttl_expires_at < now() AND is_pinned = false AND extracted_text IS NOT NULL"
    )
    return int(result.split()[-1])


@dataclass(frozen=True)
class PathGuessCacheEntry:
    found_path: str | None  # None means "cached negative": no candidate matched


async def get_path_guess(
    pool: asyncpg.Pool, root_key: str, path_kind: str
) -> PathGuessCacheEntry | None:
    row = await pool.fetchrow(
        "SELECT found_path FROM path_guess_cache "
        "WHERE root_key = $1 AND path_kind = $2 AND expires_at > now()",
        root_key,
        path_kind,
    )
    if row is None:
        return None
    return PathGuessCacheEntry(found_path=row["found_path"])


async def upsert_path_guess(
    pool: asyncpg.Pool, root_key: str, path_kind: str, *, found_path: str | None, ttl: timedelta
) -> None:
    await pool.execute(
        """
        INSERT INTO path_guess_cache (root_key, path_kind, found_path, checked_at, expires_at)
        VALUES ($1, $2, $3, now(), $4)
        ON CONFLICT (root_key, path_kind) DO UPDATE SET
            found_path = EXCLUDED.found_path,
            checked_at = EXCLUDED.checked_at,
            expires_at = EXCLUDED.expires_at
        """,
        root_key,
        path_kind,
        found_path,
        datetime.now(UTC) + ttl,
    )


async def record_path_guess_attempt(
    pool: asyncpg.Pool, root_key: str, path_kind: str, path: str, *, outcome: str
) -> None:
    await pool.execute(
        "INSERT INTO path_guess_attempts (root_key, path_kind, path, outcome) "
        "VALUES ($1, $2, $3, $4)",
        root_key,
        path_kind,
        path,
        outcome,
    )


async def path_guess_hit_rate(pool: asyncpg.Pool, *, path_kind: str | None = None) -> float | None:
    """Aggregate over `path_guess_attempts`. Returns None if there is no data yet."""
    if path_kind is None:
        row = await pool.fetchrow(
            "SELECT count(*) FILTER (WHERE outcome = 'hit') AS hits, count(*) AS total "
            "FROM path_guess_attempts"
        )
    else:
        row = await pool.fetchrow(
            "SELECT count(*) FILTER (WHERE outcome = 'hit') AS hits, count(*) AS total "
            "FROM path_guess_attempts WHERE path_kind = $1",
            path_kind,
        )
    if row is None or row["total"] == 0:
        return None
    return int(row["hits"]) / int(row["total"])
