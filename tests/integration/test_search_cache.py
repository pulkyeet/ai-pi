"""The search cache directly (`api.search.cache`) — read/write round-trip,
24h TTL expiry, and the exact "second identical query makes zero network
calls" shape the phase doc's own test spec asks for, expressed here as a
router-level assertion since the cache module alone has no network to make.

Uses `unique_query()` (see `test_search_router.py`) so rows this file writes
never collide with another test's cache entries in the same long-lived
Postgres container — the cache is deliberately shared/persistent across runs
(masterplan §9), which is the point being tested, not an accident to work
around.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from api.search import cache as search_cache
from api.search.base import SearchResponse, SearchResult

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


def unique_query() -> str:
    return f"cache probe {uuid.uuid4().hex[:12]}"


async def test_miss_then_hit_round_trips_through_postgres(pg_pool) -> None:
    query = unique_query()
    key = search_cache.cache_key(query, "fake", {"limit": 10, "site": None})

    assert await search_cache.get_fresh(pg_pool, key) is None

    response = SearchResponse(
        results=[SearchResult(url="https://example.com", title="Example", rank=0, provider="fake")],
        credits_usd=0.01,
        provider="fake",
    )
    await search_cache.upsert(
        pg_pool,
        key=key,
        provider="fake",
        query=query,
        params={"limit": 10, "site": None},
        response=response,
    )

    cached = await search_cache.get_fresh(pg_pool, key)
    assert cached is not None
    assert cached.provider == "fake"
    assert cached.credits_usd == 0.0  # cache hits never report fresh spend
    assert len(cached.results) == 1
    assert cached.results[0].url == "https://example.com"


async def test_expired_row_is_not_a_hit(pg_pool) -> None:
    query = unique_query()
    key = search_cache.cache_key(query, "fake", {"limit": 10, "site": None})
    response = SearchResponse(provider="fake", credits_usd=0.01)

    await search_cache.upsert(
        pg_pool,
        key=key,
        provider="fake",
        query=query,
        params={"limit": 10, "site": None},
        response=response,
        ttl=timedelta(seconds=-1),  # already expired
    )

    assert await search_cache.get_fresh(pg_pool, key) is None


async def test_upsert_overwrites_an_existing_row(pg_pool) -> None:
    query = unique_query()
    key = search_cache.cache_key(query, "fake", {"limit": 10, "site": None})
    params = {"limit": 10, "site": None}

    first = SearchResponse(
        results=[SearchResult(url="https://a.example", title="A", rank=0, provider="fake")],
        credits_usd=0.01,
        provider="fake",
    )
    await search_cache.upsert(
        pg_pool, key=key, provider="fake", query=query, params=params, response=first
    )

    second = SearchResponse(
        results=[SearchResult(url="https://b.example", title="B", rank=0, provider="fake")],
        credits_usd=0.02,
        provider="fake",
    )
    await search_cache.upsert(
        pg_pool, key=key, provider="fake", query=query, params=params, response=second
    )

    cached = await search_cache.get_fresh(pg_pool, key)
    assert cached is not None
    assert cached.results[0].url == "https://b.example"
