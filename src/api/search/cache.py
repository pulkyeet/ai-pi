"""The search cache: 24h TTL, shared across users (masterplan §9). Keyed by
`hash(query + provider + params)` — a second query in an already-explored
category becomes a Postgres read instead of a real provider call, which is
what makes the global daily credit cap workable at all.

Distinct from `api.retrieval.cache` (Phase 03's source cache): this table
never stores a failed/degraded outcome — a provider failure is not
memoised, it just falls through to a fresh attempt (or a degraded response)
next time. Only successful `SearchResponse`s are cached.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from api.search.base import SearchResponse

TTL = timedelta(hours=24)


def cache_key(query: str, provider: str, params: dict[str, Any]) -> str:
    """Stable regardless of `params` key order — callers may build the dict
    in any order (e.g. `limit` before or after `site`)."""
    normalized = json.dumps(
        {"query": query, "provider": provider, "params": params}, sort_keys=True, default=str
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def get_fresh(pool: asyncpg.Pool, key: str) -> SearchResponse | None:
    row = await pool.fetchrow(
        "SELECT provider, results, credits_usd FROM search_cache "
        "WHERE cache_key = $1 AND expires_at > now()",
        key,
    )
    if row is None:
        return None
    results = json.loads(row["results"]) if isinstance(row["results"], str) else row["results"]
    return SearchResponse(
        results=results,
        credits_usd=0.0,  # a cache hit spends no fresh credits
        provider=row["provider"],
    )


async def upsert(
    pool: asyncpg.Pool,
    *,
    key: str,
    provider: str,
    query: str,
    params: dict[str, Any],
    response: SearchResponse,
    ttl: timedelta = TTL,
) -> None:
    expires_at = datetime.now(UTC) + ttl
    await pool.execute(
        """
        INSERT INTO search_cache (cache_key, provider, query, params, results, credits_usd,
                                   created_at, expires_at)
        VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, now(), $7)
        ON CONFLICT (cache_key) DO UPDATE SET
            results     = EXCLUDED.results,
            credits_usd = EXCLUDED.credits_usd,
            created_at  = EXCLUDED.created_at,
            expires_at  = EXCLUDED.expires_at
        """,
        key,
        provider,
        query,
        json.dumps(params),
        json.dumps([r.model_dump() for r in response.results]),
        response.credits_usd,
        expires_at,
    )


__all__ = ["TTL", "cache_key", "get_fresh", "upsert"]
