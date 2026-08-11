"""The walking-skeleton test (Phase 10 phase doc): brief -> plan -> execute
-> claims in DB, offline and deterministic, through the *real* Phase 02
executor and the *real* Phase 07/08 resolve/persist machinery — not a
throwaway harness. Stage 0/1 LLM planning itself is exercised extensively by
Phase 09's own 58 tests already; this test starts from a real, deterministic
`fallback_plan` (masterplan's own "a run never fails because planning
failed" safety net, zero LLM calls) so the pipeline wiring under test is
`discover_competitors -> (spawn) -> profile_product -> claims`, not a second
copy of Stage 0/1's own coverage.
"""

from __future__ import annotations

import uuid

import asyncpg
import httpx
import pytest
from _db import insert_run
from _http import PLAIN_HTML, PRICING_HTML
from _tasks_http import HostRoutedTransport, exa_response, extraction_response, make_client

from api.cli import plan_to_execution_plan
from api.executor.core import Executor
from api.executor.protocol import RunFinished
from api.models.brief import ResearchBrief
from api.planner.fallback import fallback_plan
from api.retrieval.fetch import HostThrottle
from api.retrieval.robots import RobotsCache
from api.search.budget import RetrievalBudget
from api.search.exa import ExaProvider
from api.search.router import SearchRouter
from api.sources.github import GitHubRetriever
from api.sources.hn import HNRetriever
from api.sources.packages import PackagesRetriever
from api.sources.producthunt import ProductHuntRetriever
from api.sources.stackexchange import StackExchangeRetriever
from api.sources.wayback import WaybackRetriever
from api.tasks.context import HandlerDeps, RunStats
from api.tasks.registry import build_registry

pytestmark = pytest.mark.usefixtures("skip_without_postgres")

MODEL = "deepseek/deepseek-v4-flash"


def unique_domain() -> str:
    return f"t{uuid.uuid4().hex[:12]}.com"


def html_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "text/html"})


def build_deps(pool: asyncpg.Pool, transport: HostRoutedTransport, run_id: str) -> HandlerDeps:
    http = make_client(transport)
    return HandlerDeps(
        pool=pool,
        http=http,
        throttle=HostThrottle(),
        robots=RobotsCache(http, pool=pool),
        github=GitHubRetriever(http, "test-gh-token", pool=pool),
        hn=HNRetriever(http, pool=pool),
        stackexchange=StackExchangeRetriever(http, pool=pool),
        wayback=WaybackRetriever(http),
        packages=PackagesRetriever(http),
        producthunt=ProductHuntRetriever(http, None),
        search_router=SearchRouter(pool, ExaProvider(http, "test-exa-key"), run_id=run_id),
        retrieval_budget=RetrievalBudget(max_searches=50, max_fetches=50),
        run_id=run_id,
        llm_api_key="test-llm-key",
        llm_model=MODEL,
        langfuse_public_key=None,
        langfuse_secret_key=None,
        langfuse_host="https://cloud.langfuse.com",
        max_pages_per_entity=2,  # homepage + pricing only, matching what's scripted below
        max_community_threads=5,
        stats=RunStats(),
    )


async def test_full_pipeline_from_brief_to_persisted_claims(pg_pool: asyncpg.Pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)

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
    transport.add(good_domain, "/pricing", html_response(PRICING_HTML))
    # bad_domain's "/" is unscripted -> 404 -> verification fails, dropped.
    transport.add(
        "openrouter.ai",
        "/api/v1/chat/completions",
        extraction_response([]),  # homepage
        extraction_response(
            [
                {
                    "attribute": "pricing.entry_usd_month",
                    "value_num": 29,
                    "unit": "usd/month",
                    "quote": "$29/mo per seat",
                }
            ]
        ),  # pricing page
    )

    deps = build_deps(pg_pool, transport, run_id)
    registry = build_registry(deps)
    executor = Executor(pg_pool, registry, concurrency={"search": 4, "crawl": 8, "llm": 6})

    brief = ResearchBrief(
        # `api.search.cache` has no run_id in its key by design (masterplan
        # §9); a literal category here would silently replay an earlier
        # run's stale cached search results on the second+ test run — see
        # `docs/tracker.md`'s Phase 04/05/06/07 entries for the identical
        # lesson hit independently in each of those phases.
        category=f"expense tracker {uuid.uuid4().hex[:8]}",
        segment="B2B freelancers",
        geography="global",
        monetisation_guess="seat based SaaS",
        field_confidence={},
    )
    plan = fallback_plan(brief, keywords=[], max_competitors_profiled=1)
    execution_plan = plan_to_execution_plan(plan)

    events = [
        event
        async for event in executor.submit(
            run_id, execution_plan, budget_weight=plan.total_budget_weight
        )
    ]

    finished = events[-1]
    assert isinstance(finished, RunFinished)
    assert finished.failed == 0
    assert finished.done >= 2  # discover_competitors + at least one spawned profile_product

    claims = await pg_pool.fetch(
        "SELECT quote, char_start, char_end, source_id FROM claims WHERE run_id = $1", run_id
    )
    assert len(claims) >= 1
    for claim in claims:
        source_text = await pg_pool.fetchval(
            "SELECT extracted_text FROM sources WHERE id = $1", claim["source_id"]
        )
        # The core guarantee, proven end to end: every claim's stored span
        # is exactly its stored quote against the exact stored source text.
        assert source_text[claim["char_start"] : claim["char_end"]] == claim["quote"]

    bad_entity = await pg_pool.fetchrow(
        "SELECT 1 FROM entities WHERE entity_key = $1", f"web:{bad_domain}"
    )
    assert bad_entity is None
    good_entity = await pg_pool.fetchrow(
        "SELECT 1 FROM entities WHERE entity_key = $1", f"web:{good_domain}"
    )
    assert good_entity is not None
