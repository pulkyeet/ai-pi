"""`SearchRouter` against a fake `SearchProvider` — this test suite is about
the router's own mechanics (caching, budget, degradation, credit ledger),
not any real vendor, so it uses a scripted double rather than a cassette,
the same split Phase 03 drew for its own HTTP mechanics vs. Phase 01's
vendor cassettes.

Every test uses `unique_query()` rather than a literal string: the search
cache is deliberately shared/persistent across runs (masterplan §9, no
`run_id` in `cache_key`), so a hardcoded query would collide with a row a
previous test invocation left behind in this same long-lived Postgres
container — the exact reason `tests/integration/_http.py` has its own
`unique_root()` for the source cache.
"""

from __future__ import annotations

import uuid

import pytest
from _db import insert_run

from api.search.base import ProviderError, SearchResponse, SearchResult
from api.search.budget import BudgetExhaustedError, RetrievalBudget
from api.search.router import SearchRouter

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


def unique_query() -> str:
    return f"widget makers {uuid.uuid4().hex[:12]}"


class FakeProvider:
    def __init__(self, *, credits_usd: float = 0.01, raises: Exception | None = None) -> None:
        self.name = "fake"
        self.calls: list[str] = []
        self._credits_usd = credits_usd
        self._raises = raises

    async def search(
        self, query: str, *, limit: int = 10, site: str | None = None
    ) -> SearchResponse:
        self.calls.append(query)
        if self._raises is not None:
            raise self._raises
        return SearchResponse(
            results=[
                SearchResult(url="https://example.com", title="Example", rank=0, provider=self.name)
            ],
            credits_usd=self._credits_usd,
            provider=self.name,
        )


async def test_cache_hit_makes_zero_provider_calls(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    provider = FakeProvider()
    router = SearchRouter(pg_pool, provider, run_id=run_id)
    query = unique_query()

    first = await router.search(query)
    second = await router.search(query)

    assert provider.calls == [query]  # only the first call reached the provider
    assert first.results == second.results
    assert second.credits_usd == 0.0  # a cache hit spends no fresh credits


async def test_distinct_queries_both_reach_the_provider(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    provider = FakeProvider()
    router = SearchRouter(pg_pool, provider, run_id=run_id)
    query_a, query_b = unique_query(), unique_query()

    await router.search(query_a)
    await router.search(query_b)

    assert provider.calls == [query_a, query_b]


async def test_provider_error_degrades_instead_of_raising(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    provider = FakeProvider(raises=ProviderError("fake", "HTTP 503"))
    router = SearchRouter(pg_pool, provider, run_id=run_id)

    response = await router.search(unique_query())

    assert response.degraded is True
    assert response.degradation_reason is not None
    assert response.results == []


async def test_allowance_exhausted_degrades_and_never_calls_the_provider(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    provider = FakeProvider()
    router = SearchRouter(pg_pool, provider, run_id=run_id, daily_credit_cap_usd=0.0)

    response = await router.search(unique_query())

    assert response.degraded is True
    assert provider.calls == []  # the allowance gate runs before the provider is ever called


async def test_budget_exhaustion_raises_out_of_the_router(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    provider = FakeProvider()
    router = SearchRouter(pg_pool, provider, run_id=run_id)
    budget = RetrievalBudget(max_searches=1, max_fetches=1)

    await router.search(unique_query(), budget=budget)
    with pytest.raises(BudgetExhaustedError):
        await router.search(unique_query(), budget=budget)


async def test_successful_call_records_credit_usage(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    provider = FakeProvider(credits_usd=0.007)
    router = SearchRouter(pg_pool, provider, run_id=run_id)

    await router.search(unique_query())

    row = await pg_pool.fetchrow(
        "SELECT provider, credits_usd FROM search_credit_usage WHERE run_id = $1", run_id
    )
    assert row is not None
    assert row["provider"] == "fake"
    assert float(row["credits_usd"]) == 0.007


async def test_degraded_response_is_never_cached(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    provider = FakeProvider(raises=ProviderError("fake", "HTTP 503"))
    router = SearchRouter(pg_pool, provider, run_id=run_id)
    query = unique_query()

    await router.search(query)
    provider._raises = None  # the vendor recovers

    response = await router.search(query)

    assert response.degraded is False
    assert provider.calls == [query, query]  # not served from cache
