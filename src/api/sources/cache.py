"""Persistent 24h cache for domain retrievers (Phase 14 follow-up, 2026-08-10).

Masterplan §9 specifies three cache types (source 7d, search 24h, extraction
permanent); HN Algolia and GitHub Search fell outside all three, and
`RobotsCache` was in-memory only — so a fresh `--cached-only` process (a
new `bench.runner` invocation, a CI job) still made live calls and the
zero-spend replay promise failed. This fills the gap with one
`retriever_cache` table keyed by `sha256(provider + params)`.

Mirrors `api.search.cache`'s discipline exactly: only *successful*
responses are stored (a failure is never memoised — it falls through to a
fresh attempt next time), entries expire after 24h, and a hit spends zero
fresh credits.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

TTL = timedelta(hours=24)


def cache_key(provider: str, *parts: str) -> str:
    """Stable across calls — `parts` must be hashable strings (query, params)."""
    normalized = json.dumps([provider, *parts], sort_keys=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def get_fresh(pool: asyncpg.Pool, key: str) -> Any | None:
    row = await pool.fetchrow(
        "SELECT payload FROM retriever_cache WHERE cache_key = $1 AND expires_at > now()",
        key,
    )
    if row is None:
        return None
    payload = row["payload"]
    return json.loads(payload) if isinstance(payload, str) else payload


async def upsert(
    pool: asyncpg.Pool, *, key: str, provider: str, payload: Any, ttl: timedelta = TTL
) -> None:
    expires_at = datetime.now(UTC) + ttl
    await pool.execute(
        """
        INSERT INTO retriever_cache (cache_key, provider, payload, created_at, expires_at)
        VALUES ($1, $2, $3::jsonb, now(), $4)
        ON CONFLICT (cache_key) DO UPDATE SET
            payload    = EXCLUDED.payload,
            created_at = EXCLUDED.created_at,
            expires_at = EXCLUDED.expires_at
        """,
        key,
        provider,
        json.dumps(payload),
        expires_at,
    )


__all__ = ["TTL", "cache_key", "get_fresh", "upsert"]
