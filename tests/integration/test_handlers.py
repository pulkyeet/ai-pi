"""Phase 10 phase doc's "Each handler in isolation against cassettes" row —
offline, deterministic, via `_tasks_http.HostRoutedTransport` (no real
vendor call, since these vendors have no committed cassette shaped for
these specific interactions) plus real Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest
from _db import insert_run, insert_task
from _http import PLAIN_HTML, PRICING_HTML
from _tasks_http import (
    HostRoutedTransport,
    exa_response,
    extraction_response,
    github_repo_response,
    make_client,
)

from api.executor.protocol import ExecutorEvent, TaskContext
from api.resolve.store import upsert_entity
from api.retrieval.fetch import HostThrottle
from api.retrieval.robots import RobotsCache
from api.search.budget import RetrievalBudget
from api.search.exa import ExaProvider
from api.search.router import SearchRouter
from api.sources.github import GitHubRetriever
from api.sources.hn import HNRetriever
from api.sources.packages import PackagesRetriever
from api.sources.producthunt import ProductHuntRetriever
from api.sources.reddit import RedditRetriever
from api.sources.stackexchange import StackExchangeRetriever
from api.sources.wayback import WaybackRetriever
from api.tasks.community import MineCommunityHandler
from api.tasks.context import HandlerDeps, RunStats
from api.tasks.discover import DiscoverCompetitorsHandler
from api.tasks.funding import FindFundingHandler
from api.tasks.oss import OssProfileHandler
from api.tasks.pricing import ExtractPricingHandler
from api.tasks.profile import ProfileProductHandler, extract_snippet_claims
from api.tasks.trends import TrendSignalsHandler

pytestmark = pytest.mark.usefixtures("skip_without_postgres")

MODEL = "deepseek/deepseek-v4-flash"


def unique_domain() -> str:
    return f"t{uuid.uuid4().hex[:12]}.com"


def unique_query(base: str) -> str:
    """`api.search.cache` has no `run_id` in its key by design (masterplan
    §9: a repeat query is nearly free, even across runs) — reusing a literal
    query string across tests silently serves a previous test's cached
    result. See `docs/tracker.md`'s Phase 04/05/06/07 entries for the same
    lesson hit independently in each of those phases; same fix here."""
    return f"{base} {uuid.uuid4().hex[:8]}"


def build_deps(
    pool: asyncpg.Pool, transport: HostRoutedTransport, run_id: str, *, max_pages: int = 2
) -> HandlerDeps:
    http = make_client(transport)
    throttle = HostThrottle()
    robots = RobotsCache(http, pool=pool)
    return HandlerDeps(
        pool=pool,
        http=http,
        throttle=throttle,
        robots=robots,
        github=GitHubRetriever(http, "test-gh-token", pool=pool),
        hn=HNRetriever(http, pool=pool),
        stackexchange=StackExchangeRetriever(http, pool=pool),
        wayback=WaybackRetriever(http),
        packages=PackagesRetriever(http),
        producthunt=ProductHuntRetriever(http, None),
        reddit=RedditRetriever(http, enabled=False, client_id=None, client_secret=None),
        search_router=SearchRouter(pool, ExaProvider(http, "test-exa-key"), run_id=run_id),
        retrieval_budget=RetrievalBudget(max_searches=50, max_fetches=50),
        run_id=run_id,
        llm_api_key="test-llm-key",
        llm_model=MODEL,
        langfuse_public_key=None,
        langfuse_secret_key=None,
        langfuse_host="https://cloud.langfuse.com",
        max_pages_per_entity=max_pages,
        max_community_threads=5,
        stats=RunStats(),
    )


def task_ctx(run_id: str, task_id: int, node_key: str, kind: str) -> TaskContext:
    async def _emit(event: ExecutorEvent) -> None:
        return None

    async def _renew() -> bool:
        return True

    return TaskContext(
        run_id=run_id,
        task_id=task_id,
        node_key=node_key,
        kind=kind,
        lease_token=uuid.uuid4(),
        attempt=1,
        emit=_emit,
        renew_lease=_renew,
    )


def html_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "text/html"})


class TestDiscoverCompetitors:
    async def test_hallucinated_candidate_dropped_verified_one_spawned(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "t1", kind="discover_competitors")

        good_domain = unique_domain()
        bad_domain = unique_domain()

        transport = HostRoutedTransport()
        transport.add(
            "api.exa.ai",
            "/search",
            exa_response(
                [
                    {"url": f"https://{good_domain}/", "title": "Good Co"},
                    {"url": f"https://{bad_domain}/", "title": "Hallucinated Co"},
                ]
            ),
        )
        transport.add(good_domain, "/", html_response(PLAIN_HTML))
        # bad_domain's "/" is left unscripted -> 404 -> verification fails.

        deps = build_deps(pg_pool, transport, run_id)
        handler = DiscoverCompetitorsHandler(deps)
        ctx = task_ctx(run_id, task_id, "t1", "discover_competitors")

        result = await handler.run(
            ctx, {"query_variants": [unique_query("widget maker")], "max_profile_count": 5}
        )

        assert deps.stats.entities_verified == 1
        assert deps.stats.entities_rejected == 1
        assert len(result.spawned) == 1
        assert result.spawned[0].kind == "profile_product"
        assert result.spawned[0].args["entity_key"] == f"web:{good_domain}"

        bad_row = await pg_pool.fetchrow(
            "SELECT 1 FROM entities WHERE entity_key = $1", f"web:{bad_domain}"
        )
        assert bad_row is None
        good_row = await pg_pool.fetchrow(
            "SELECT 1 FROM entities WHERE entity_key = $1", f"web:{good_domain}"
        )
        assert good_row is not None

    async def test_fanout_bounded_by_max_profile_count(self, pg_pool: asyncpg.Pool) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "t1", kind="discover_competitors")

        domains = [unique_domain() for _ in range(3)]
        transport = HostRoutedTransport()
        transport.add(
            "api.exa.ai",
            "/search",
            exa_response([{"url": f"https://{d}/", "title": d} for d in domains]),
        )
        for d in domains:
            transport.add(d, "/", html_response(PLAIN_HTML))

        deps = build_deps(pg_pool, transport, run_id)
        handler = DiscoverCompetitorsHandler(deps)
        ctx = task_ctx(run_id, task_id, "t1", "discover_competitors")

        result = await handler.run(
            ctx, {"query_variants": [unique_query("widget maker")], "max_profile_count": 2}
        )

        full_profiles = [s for s in result.spawned if s.kind == "profile_product"]
        pricing_only = [s for s in result.spawned if s.kind == "extract_pricing"]
        assert len(full_profiles) == 2
        assert len(pricing_only) == 1  # the pricing-only tier below max_profile_count

    async def test_transport_failure_on_one_candidate_does_not_crash_discovery(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """A real live-run finding: `api.retrieval.fetch_source` only wraps
        *timeouts* in a typed `FetchError` (Phase 03); a raw DNS/connection
        failure for one candidate propagates as `httpx.ConnectError`. One
        bad candidate must not take down the whole task."""
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "t1", kind="discover_competitors")

        good_domain = unique_domain()
        unreachable_domain = unique_domain()

        transport = HostRoutedTransport()
        transport.add(
            "api.exa.ai",
            "/search",
            exa_response(
                [
                    {"url": f"https://{good_domain}/", "title": "Good Co"},
                    {"url": f"https://{unreachable_domain}/", "title": "Unreachable Co"},
                ]
            ),
        )
        transport.add(good_domain, "/", html_response(PLAIN_HTML))
        transport.add(
            unreachable_domain,
            "/",
            httpx.ConnectError("[Errno -2] Name or service not known"),
        )

        deps = build_deps(pg_pool, transport, run_id)
        handler = DiscoverCompetitorsHandler(deps)
        ctx = task_ctx(run_id, task_id, "t1", "discover_competitors")

        result = await handler.run(
            ctx, {"query_variants": [unique_query("widget maker")], "max_profile_count": 5}
        )

        assert deps.stats.entities_verified == 1
        assert deps.stats.entities_rejected == 1
        assert len(result.spawned) == 1
        assert result.spawned[0].args["entity_key"] == f"web:{good_domain}"

    async def test_consider_oss_seeds_awesome_repo_search_and_spawns_oss_profile(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "t1", kind="discover_competitors")

        repo = f"acme/{uuid.uuid4().hex[:8]}"
        owner, name = repo.split("/")
        query = unique_query("widget maker")

        transport = HostRoutedTransport()
        transport.add("api.exa.ai", "/search", exa_response([]))
        transport.add(
            "api.github.com",
            "/search/repositories",
            httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "full_name": repo,
                            "html_url": f"https://github.com/{repo}",
                            "description": "a widget maker",
                            "stargazers_count": 42,
                        }
                    ]
                },
            ),
        )
        transport.add(
            "api.github.com", f"/repos/{owner}/{name}", github_repo_response(full_name=repo)
        )
        transport.add(
            "api.github.com",
            f"/repos/{owner}/{name}/contributors",
            httpx.Response(200, json=[]),
        )

        deps = build_deps(pg_pool, transport, run_id)
        handler = DiscoverCompetitorsHandler(deps)
        ctx = task_ctx(run_id, task_id, "t1", "discover_competitors")

        result = await handler.run(
            ctx, {"query_variants": [query], "max_profile_count": 5, "consider_oss": True}
        )

        oss_spawns = [s for s in result.spawned if s.kind == "oss_profile"]
        assert len(oss_spawns) == 1
        assert oss_spawns[0].args["repo"] == repo

    async def test_awesome_repo_search_failure_does_not_fail_discovery(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "t1", kind="discover_competitors")

        query = unique_query("widget maker")
        transport = HostRoutedTransport()
        transport.add("api.exa.ai", "/search", exa_response([]))
        # "/search/repositories" left unscripted -> 404 -> HTTPStatusError, swallowed.

        deps = build_deps(pg_pool, transport, run_id)
        handler = DiscoverCompetitorsHandler(deps)
        ctx = task_ctx(run_id, task_id, "t1", "discover_competitors")

        result = await handler.run(
            ctx, {"query_variants": [query], "max_profile_count": 5, "consider_oss": True}
        )
        assert result.spawned == []


class TestProfileProduct:
    async def test_persists_graded_pricing_claim(self, pg_pool: asyncpg.Pool) -> None:
        domain = unique_domain()
        entity_key = f"web:{domain}"
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "profile:1", kind="profile_product")
        await upsert_entity(
            pg_pool, entity_key=entity_key, display_name=domain, maturity=None, meta={}
        )

        transport = HostRoutedTransport()
        transport.add(domain, "/", html_response(PLAIN_HTML))
        transport.add(domain, "/pricing", html_response(PRICING_HTML))
        transport.add(
            "openrouter.ai",
            "/api/v1/chat/completions",
            extraction_response([]),  # homepage: nothing extractable
            extraction_response(
                [
                    {
                        "attribute": "pricing.entry_usd_month",
                        "value_num": 29,
                        "unit": "usd/month",
                        "quote": "$29/mo per seat",
                    }
                ]
            ),
        )

        deps = build_deps(pg_pool, transport, run_id, max_pages=2)
        handler = ProfileProductHandler(deps)
        ctx = task_ctx(run_id, task_id, "profile:1", "profile_product")

        result = await handler.run(ctx, {"entity_key": entity_key})

        assert result.artifacts["claims_persisted"] == 1
        row = await pg_pool.fetchrow(
            "SELECT c.attribute, c.value_num, c.grade, c.confidence, c.quote, "
            "c.char_start, c.char_end, s.extracted_text "
            "FROM claims c JOIN sources s ON s.id = c.source_id "
            "WHERE c.run_id = $1",
            run_id,
        )
        assert row is not None
        assert row["attribute"] == "pricing.entry_usd_month"
        assert row["value_num"] == 29
        assert row["grade"] == "A"  # own-domain, non-prose path
        assert 0 < row["confidence"] <= 1
        # The core guarantee: the stored span is exactly the stored quote.
        assert row["extracted_text"][row["char_start"] : row["char_end"]] == row["quote"]

    async def test_snippet_claims_persist_grade_c_with_pricing_dropped(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """06b 'quote Exa': a search-result snippet becomes a grade-C synthetic
        source. Non-pricing claims bind against the stored snippet text; pricing
        claims are dropped so snippets can never complete the pricing triple."""
        domain = unique_domain()
        entity_key = f"web:{domain}"
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "profile:1", kind="profile_product")
        entity = await upsert_entity(
            pg_pool, entity_key=entity_key, display_name=domain, maturity=None, meta={}
        )

        snippet = (
            f"Acme ships an expense tracker for iOS and Android teams. "
            f"Plans start at $29/mo per seat. {uuid.uuid4().hex[:8]}"
        )
        transport = HostRoutedTransport()
        transport.add(
            "openrouter.ai",
            "/api/v1/chat/completions",
            extraction_response(
                [
                    {
                        "attribute": "pricing.entry_usd_month",
                        "value_num": 29,
                        "unit": "usd/month",
                        "quote": "$29/mo per seat",
                    },
                    {"attribute": "product.platforms", "value_text": "ios", "quote": "iOS"},
                ]
            ),
        )
        deps = build_deps(pg_pool, transport, run_id)
        ctx = task_ctx(run_id, task_id, "profile:1", "profile_product")

        n = await extract_snippet_claims(
            deps, ctx, entity_id=entity.id, root_key=domain, snippet=snippet
        )

        assert n == 1
        row = await pg_pool.fetchrow(
            "SELECT c.attribute, c.value_text, c.grade, c.quote, c.char_start, c.char_end, "
            "s.retrieval_reason, s.extracted_text "
            "FROM claims c JOIN sources s ON s.id = c.source_id "
            "WHERE c.run_id = $1",
            run_id,
        )
        assert row is not None
        assert row["attribute"] == "product.platforms"
        assert row["value_text"] == "ios"
        assert row["grade"] == "C"
        assert row["retrieval_reason"] == "serp_snippet"
        assert row["extracted_text"][row["char_start"] : row["char_end"]] == row["quote"]
        pricing = await pg_pool.fetchval(
            "SELECT count(*) FROM claims WHERE run_id = $1 AND "
            "attribute = 'pricing.entry_usd_month'",
            run_id,
        )
        assert pricing == 0

    async def test_empty_snippet_is_a_no_op(self, pg_pool: asyncpg.Pool) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "profile:1", kind="profile_product")
        deps = build_deps(pg_pool, HostRoutedTransport(), run_id)
        ctx = task_ctx(run_id, task_id, "profile:1", "profile_product")
        n = await extract_snippet_claims(
            deps, ctx, entity_id=0, root_key="unused.example", snippet="   "
        )
        assert n == 0

    async def test_handler_idempotent_on_rerun(self, pg_pool: asyncpg.Pool) -> None:
        domain = unique_domain()
        entity_key = f"web:{domain}"
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id_1 = await insert_task(conn, run_id, "profile:1", kind="profile_product")
            task_id_2 = await insert_task(conn, run_id, "profile:2", kind="profile_product")
        await upsert_entity(
            pg_pool, entity_key=entity_key, display_name=domain, maturity=None, meta={}
        )

        transport = HostRoutedTransport()
        transport.add(domain, "/", html_response(PLAIN_HTML))
        transport.add(domain, "/pricing", html_response(PRICING_HTML))
        transport.add(
            "openrouter.ai",
            "/api/v1/chat/completions",
            extraction_response([]),
            extraction_response(
                [
                    {
                        "attribute": "pricing.entry_usd_month",
                        "value_num": 29,
                        "unit": "usd/month",
                        "quote": "$29/mo per seat",
                    }
                ]
            ),
        )

        deps = build_deps(pg_pool, transport, run_id, max_pages=2)
        handler = ProfileProductHandler(deps)

        first = await handler.run(
            task_ctx(run_id, task_id_1, "profile:1", "profile_product"), {"entity_key": entity_key}
        )
        # Second run: fetch/search/extraction caches all hit, so the mock
        # transport's queue (already exhausted above) is never touched again.
        second = await handler.run(
            task_ctx(run_id, task_id_2, "profile:2", "profile_product"), {"entity_key": entity_key}
        )

        assert first.artifacts["claims_persisted"] == 1
        assert second.artifacts["claims_persisted"] == 0  # ON CONFLICT DO NOTHING

        count = await pg_pool.fetchval(
            "SELECT count(*) FROM claims WHERE run_id = $1 AND entity_id = "
            "(SELECT id FROM entities WHERE entity_key = $2)",
            run_id,
            entity_key,
        )
        assert count == 1

    async def test_unknown_entity_is_a_no_op(self, pg_pool: asyncpg.Pool) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "profile:3", kind="profile_product")
        deps = build_deps(pg_pool, HostRoutedTransport(), run_id)
        handler = ProfileProductHandler(deps)
        ctx = task_ctx(run_id, task_id, "profile:3", "profile_product")

        result = await handler.run(ctx, {"entity_key": "web:does-not-exist.example"})
        assert result.artifacts == {}

    async def test_non_web_entity_is_a_no_op(self, pg_pool: asyncpg.Pool) -> None:
        repo = f"acme/{uuid.uuid4().hex[:8]}"
        entity_key = f"gh:{repo}"
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "profile:4", kind="profile_product")
        await upsert_entity(
            pg_pool, entity_key=entity_key, display_name=repo, maturity=None, meta={}
        )
        deps = build_deps(pg_pool, HostRoutedTransport(), run_id)
        handler = ProfileProductHandler(deps)
        ctx = task_ctx(run_id, task_id, "profile:4", "profile_product")

        result = await handler.run(ctx, {"entity_key": entity_key})
        assert result.artifacts == {}

    async def test_fetch_budget_exhaustion_skips_remaining_pages_gracefully(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        domain = unique_domain()
        entity_key = f"web:{domain}"
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "profile:5", kind="profile_product")
        await upsert_entity(
            pg_pool, entity_key=entity_key, display_name=domain, maturity=None, meta={}
        )

        deps = build_deps(pg_pool, HostRoutedTransport(), run_id, max_pages=2)
        deps.retrieval_budget = RetrievalBudget(max_searches=50, max_fetches=0)
        handler = ProfileProductHandler(deps)
        ctx = task_ctx(run_id, task_id, "profile:5", "profile_product")

        result = await handler.run(ctx, {"entity_key": entity_key})
        assert result.artifacts["claims_persisted"] == 0


class TestOssProfile:
    async def test_persists_structured_claims_and_degrades_star_velocity_403(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        repo = f"acme/{uuid.uuid4().hex[:8]}"
        owner, name = repo.split("/")
        entity_key = f"gh:{repo}"
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "oss:1", kind="oss_profile")
        await upsert_entity(
            pg_pool, entity_key=entity_key, display_name=repo, maturity=None, meta={}
        )

        transport = HostRoutedTransport()
        transport.add(
            "api.github.com",
            f"/repos/{owner}/{name}",
            github_repo_response(full_name=repo, stars=250, license_spdx="MIT"),
        )
        transport.add(
            "api.github.com",
            f"/repos/{owner}/{name}/contributors",
            httpx.Response(200, json=[{"login": "a"}]),
        )
        transport.add("api.github.com", f"/repos/{owner}/{name}/stargazers", httpx.Response(403))

        deps = build_deps(pg_pool, transport, run_id)
        handler = OssProfileHandler(deps)
        ctx = task_ctx(run_id, task_id, "oss:1", "oss_profile")

        result = await handler.run(ctx, {"repo": repo})
        assert result.artifacts["claims_persisted"] >= 2  # at least oss.repo, oss.stars

        rows = await pg_pool.fetch(
            "SELECT attribute, value_num, value_text, grade FROM claims "
            "WHERE run_id = $1 AND entity_id = (SELECT id FROM entities WHERE entity_key = $2)",
            run_id,
            entity_key,
        )
        by_attr = {r["attribute"]: r for r in rows}
        assert by_attr["oss.stars"]["value_num"] == 250
        assert by_attr["oss.stars"]["grade"] == "A"
        assert by_attr["oss.license"]["value_text"] == "MIT"
        # Known, documented gaps: never populated by this handler.
        assert "oss.stars_90d_delta" not in by_attr
        assert "oss.contributors_90d" not in by_attr

    async def test_malformed_repo_arg_is_a_no_op(self, pg_pool: asyncpg.Pool) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "oss:2", kind="oss_profile")
        deps = build_deps(pg_pool, HostRoutedTransport(), run_id)
        handler = OssProfileHandler(deps)
        ctx = task_ctx(run_id, task_id, "oss:2", "oss_profile")

        result = await handler.run(ctx, {"repo": "not-a-repo"})
        assert result.artifacts == {}

    async def test_unknown_entity_is_a_no_op(self, pg_pool: asyncpg.Pool) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "oss:3", kind="oss_profile")
        deps = build_deps(pg_pool, HostRoutedTransport(), run_id)
        handler = OssProfileHandler(deps)
        ctx = task_ctx(run_id, task_id, "oss:3", "oss_profile")

        result = await handler.run(ctx, {"repo": "acme/never-resolved"})
        assert result.artifacts == {}

    async def test_repo_metadata_failure_degrades_gracefully(self, pg_pool: asyncpg.Pool) -> None:
        repo = f"acme/{uuid.uuid4().hex[:8]}"
        entity_key = f"gh:{repo}"
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "oss:4", kind="oss_profile")
        await upsert_entity(
            pg_pool, entity_key=entity_key, display_name=repo, maturity=None, meta={}
        )

        # repo_metadata's endpoint is left unscripted -> 404 -> HTTPStatusError.
        deps = build_deps(pg_pool, HostRoutedTransport(), run_id)
        handler = OssProfileHandler(deps)
        ctx = task_ctx(run_id, task_id, "oss:4", "oss_profile")

        result = await handler.run(ctx, {"repo": repo})
        assert result.artifacts == {}


class TestFindFunding:
    async def test_no_search_results_is_a_normal_no_op(self, pg_pool: asyncpg.Pool) -> None:
        domain = unique_domain()
        entity_key = f"web:{domain}"
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "funding:1", kind="find_funding")
        await upsert_entity(
            pg_pool,
            entity_key=entity_key,
            display_name=unique_query("Acme"),
            maturity=None,
            meta={},
        )

        transport = HostRoutedTransport()
        transport.add("api.exa.ai", "/search", exa_response([]))

        deps = build_deps(pg_pool, transport, run_id)
        handler = FindFundingHandler(deps)
        ctx = task_ctx(run_id, task_id, "funding:1", "find_funding")

        result = await handler.run(ctx, {"entity_key": entity_key})
        assert result.artifacts.get("claims_persisted", 0) == 0


class TestMineCommunity:
    async def test_persists_complaint_claim_to_category_entity(self, pg_pool: asyncpg.Pool) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "community:1", kind="mine_community")

        transport = HostRoutedTransport()
        transport.add(
            "hn.algolia.com",
            "/api/v1/search",
            httpx.Response(
                200,
                json={
                    "hits": [
                        {
                            "title": "Why does my expense tracker keep losing receipts",
                            "url": "https://example.com/x",
                            "points": 10,
                            "num_comments": 3,
                            "created_at": "2026-01-01T00:00:00Z",
                        }
                    ]
                },
            ),
        )
        transport.add(
            "openrouter.ai",
            "/api/v1/chat/completions",
            extraction_response(
                [
                    {
                        "attribute": "complaint.receipt-loss",
                        "value_text": "lost receipts",
                        "quote": "Why does my expense tracker keep losing receipts",
                    }
                ]
            ),
        )

        deps = build_deps(pg_pool, transport, run_id)
        handler = MineCommunityHandler(deps)
        ctx = task_ctx(run_id, task_id, "community:1", "mine_community")

        result = await handler.run(ctx, {"keywords": ["expense tracker"], "venues": ["hn"]})
        assert result.artifacts["claims_persisted"] == 1

        entity = await pg_pool.fetchrow(
            "SELECT id FROM entities WHERE entity_key = $1", f"category:{run_id}"
        )
        assert entity is not None
        claim = await pg_pool.fetchrow(
            "SELECT attribute, grade FROM claims WHERE run_id = $1 AND entity_id = $2",
            run_id,
            entity["id"],
        )
        assert claim is not None
        assert claim["attribute"] == "complaint.receipt-loss"
        assert claim["grade"] == "D"

    async def test_reddit_venue_disabled_degrades_without_crashing(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "community:2", kind="mine_community")

        transport = HostRoutedTransport()  # nothing scripted; reddit must never be called
        deps = build_deps(pg_pool, transport, run_id)
        handler = MineCommunityHandler(deps)
        ctx = task_ctx(run_id, task_id, "community:2", "mine_community")

        result = await handler.run(
            ctx, {"keywords": [unique_query("crm")], "venues": ["reddit", "made-up-venue"]}
        )
        assert result.artifacts.get("claims_persisted", 0) == 0

    async def test_empty_keywords_or_venues_is_a_no_op(self, pg_pool: asyncpg.Pool) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "community:3", kind="mine_community")
        deps = build_deps(pg_pool, HostRoutedTransport(), run_id)
        handler = MineCommunityHandler(deps)
        ctx = task_ctx(run_id, task_id, "community:3", "mine_community")

        result = await handler.run(ctx, {"keywords": [], "venues": ["hn"]})
        assert result.artifacts == {}


class TestExtractPricing:
    async def test_persists_graded_pricing_claim(self, pg_pool: asyncpg.Pool) -> None:
        domain = unique_domain()
        entity_key = f"web:{domain}"
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "pricing:1", kind="extract_pricing")
        await upsert_entity(
            pg_pool, entity_key=entity_key, display_name=domain, maturity=None, meta={}
        )

        transport = HostRoutedTransport()
        transport.add(domain, "/pricing", html_response(PRICING_HTML))
        transport.add(
            "openrouter.ai",
            "/api/v1/chat/completions",
            extraction_response(
                [
                    {
                        "attribute": "pricing.entry_usd_month",
                        "value_num": 29,
                        "unit": "usd/month",
                        "quote": "$29/mo per seat",
                    }
                ]
            ),
        )

        deps = build_deps(pg_pool, transport, run_id)
        handler = ExtractPricingHandler(deps)
        ctx = task_ctx(run_id, task_id, "pricing:1", "extract_pricing")

        result = await handler.run(ctx, {"entity_key": entity_key})
        assert result.artifacts["claims_persisted"] == 1

    async def test_no_pricing_page_found_persists_nothing(self, pg_pool: asyncpg.Pool) -> None:
        domain = unique_domain()
        entity_key = f"web:{domain}"
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "pricing:2", kind="extract_pricing")
        await upsert_entity(
            pg_pool, entity_key=entity_key, display_name=domain, maturity=None, meta={}
        )

        # No pricing paths scripted -> every guess 404s -> negative cache, no claims.
        deps = build_deps(pg_pool, HostRoutedTransport(), run_id)
        handler = ExtractPricingHandler(deps)
        ctx = task_ctx(run_id, task_id, "pricing:2", "extract_pricing")

        result = await handler.run(ctx, {"entity_key": entity_key})
        assert result.artifacts["claims_persisted"] == 0

    async def test_unknown_entity_is_a_no_op(self, pg_pool: asyncpg.Pool) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "pricing:3", kind="extract_pricing")
        deps = build_deps(pg_pool, HostRoutedTransport(), run_id)
        handler = ExtractPricingHandler(deps)
        ctx = task_ctx(run_id, task_id, "pricing:3", "extract_pricing")

        result = await handler.run(ctx, {"entity_key": "web:does-not-exist.example"})
        assert result.artifacts == {}

    async def test_non_web_entity_is_a_no_op(self, pg_pool: asyncpg.Pool) -> None:
        repo = f"acme/{uuid.uuid4().hex[:8]}"
        entity_key = f"gh:{repo}"
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "pricing:4", kind="extract_pricing")
        await upsert_entity(
            pg_pool, entity_key=entity_key, display_name=repo, maturity=None, meta={}
        )
        deps = build_deps(pg_pool, HostRoutedTransport(), run_id)
        handler = ExtractPricingHandler(deps)
        ctx = task_ctx(run_id, task_id, "pricing:4", "extract_pricing")

        result = await handler.run(ctx, {"entity_key": entity_key})
        assert result.artifacts == {}


class TestFindFundingFound:
    async def test_persists_funding_claim_when_found(self, pg_pool: asyncpg.Pool) -> None:
        domain = unique_domain()
        entity_key = f"web:{domain}"
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "funding:2", kind="find_funding")
        await upsert_entity(
            pg_pool,
            entity_key=entity_key,
            display_name=unique_query("Acme"),
            maturity=None,
            meta={},
        )

        news_domain = unique_domain()
        news_html = (
            b"<html><body><main><h1>Funding news</h1><p>The company today announced it "
            b"raised $5,000,000 in seed funding to expand its engineering team and grow "
            b"into new markets over the coming year, according to people familiar with "
            b"the matter.</p></main></body></html>"
        )
        transport = HostRoutedTransport()
        transport.add(
            "api.exa.ai", "/search", exa_response([{"url": f"https://{news_domain}/news"}])
        )
        transport.add(news_domain, "/news", html_response(news_html))
        transport.add(
            "openrouter.ai",
            "/api/v1/chat/completions",
            extraction_response(
                [
                    {
                        "attribute": "company.funding_total_usd",
                        "value_num": 5_000_000,
                        "unit": "usd",
                        "quote": "raised $5,000,000 in seed funding",
                    }
                ]
            ),
        )

        deps = build_deps(pg_pool, transport, run_id)
        handler = FindFundingHandler(deps)
        ctx = task_ctx(run_id, task_id, "funding:2", "find_funding")

        result = await handler.run(ctx, {"entity_key": entity_key})
        assert result.artifacts["claims_persisted"] == 1

        row = await pg_pool.fetchrow(
            "SELECT grade FROM claims WHERE run_id = $1 "
            "AND attribute = 'company.funding_total_usd'",
            run_id,
        )
        assert row is not None
        assert row["grade"] == "C"  # third-party reporting, not the entity's own domain

    async def test_unknown_entity_is_a_no_op(self, pg_pool: asyncpg.Pool) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "funding:3", kind="find_funding")
        deps = build_deps(pg_pool, HostRoutedTransport(), run_id)
        handler = FindFundingHandler(deps)
        ctx = task_ctx(run_id, task_id, "funding:3", "find_funding")

        result = await handler.run(ctx, {"entity_key": "web:does-not-exist.example"})
        assert result.artifacts == {}


class TestTrendSignals:
    async def test_reports_hn_volume_and_pageviews_via_artifacts_only(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "trends:1", kind="trend_signals")

        keyword = unique_query("expense tracker")
        article = "_".join(w.capitalize() for w in keyword.split())
        end = datetime.now(UTC)
        start = end - timedelta(days=365)
        pageviews_path = (
            "/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/all-agents/"
            f"{article}/monthly/{start.strftime('%Y%m01')}/{end.strftime('%Y%m01')}"
        )

        transport = HostRoutedTransport()
        transport.add(
            "hn.algolia.com",
            "/api/v1/search_by_date",
            httpx.Response(200, json={"hits": [{"title": "x"}, {"title": "y"}]}),
        )
        transport.add(
            "wikimedia.org",
            pageviews_path,
            httpx.Response(200, json={"items": [{"views": 100}, {"views": 50}]}),
        )

        deps = build_deps(pg_pool, transport, run_id)
        handler = TrendSignalsHandler(deps)
        ctx = task_ctx(run_id, task_id, "trends:1", "trend_signals")

        result = await handler.run(ctx, {"keywords": [keyword]})

        # No claim vocabulary slot exists for trend data (documented gap) —
        # this handler must never write to `claims`.
        count = await pg_pool.fetchval("SELECT count(*) FROM claims WHERE run_id = $1", run_id)
        assert count == 0
        assert result.artifacts["hn_post_volume"][keyword] == 2

    async def test_no_keywords_is_a_no_op(self, pg_pool: asyncpg.Pool) -> None:
        async with pg_pool.acquire() as conn:
            run_id = await insert_run(conn)
            task_id = await insert_task(conn, run_id, "trends:2", kind="trend_signals")
        deps = build_deps(pg_pool, HostRoutedTransport(), run_id)
        handler = TrendSignalsHandler(deps)
        ctx = task_ctx(run_id, task_id, "trends:2", "trend_signals")

        result = await handler.run(ctx, {"keywords": []})
        assert result.artifacts == {}
