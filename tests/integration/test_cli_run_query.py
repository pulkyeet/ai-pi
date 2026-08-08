"""Phase 14: `api.cli.run_query` is the extraction that gives `bench.runner`
a programmatic, in-process entry point into the real pipeline (previously
`cmd_run` only printed — no return value, no way for a caller other than a
human reading stdout to know the `run_id`/cost/coverage/report). This test
exercises the *whole* thing end to end (interpret -> plan -> execute ->
persist -> assemble), unlike `test_pipeline_e2e.py`'s walking skeleton, which
deliberately starts from a real `fallback_plan` and stops at claim
persistence. Fully offline and deterministic: every OpenRouter/Exa/vendor
call is scripted.
"""

from __future__ import annotations

import json
import uuid

import asyncpg
import httpx
import pytest
from _tasks_http import HostRoutedTransport, chat_response, exa_response, extraction_response

from api.cli import RunOutcome, run_query
from api.config import Settings

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


def unique_domain() -> str:
    return f"t{uuid.uuid4().hex[:12]}.com"


def html_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "text/html"})


def pricing_html(marker: str) -> bytes:
    # `api.extract.cache`/`api.llm.cache` are permanent, keyed on page/prompt
    # content — a literal, repeated-across-test-runs body would silently
    # replay a *previous* run's cached extraction instead of hitting this
    # test's own scripted response (the same lesson every phase since 04 has
    # hit; see docs/working_knowledge.md's Known Issues). `marker` keeps the
    # extracted text, and therefore its content_hash, unique per test call.
    # (`tests/integration/_http.py`'s shared `PLAIN_HTML`/`PRICING_HTML`
    # constants are deliberately *not* reused here for the same reason: this
    # test needs both the homepage and pricing extraction calls to be real,
    # deterministic cache misses that consume this test's own scripted
    # response, not whatever an earlier test in the same suite run already
    # cached for that exact literal content.)
    return (
        f"<html><body><main><h1>Pricing</h1><p>Pro plan: $29/mo per seat. "
        f"A free tier is also available for small teams evaluating the "
        f"product before committing to a paid plan. (ref-{marker})</p>"
        f"</main></body></html>"
    ).encode()


def homepage_html(marker: str) -> bytes:
    return (
        f"<html><body><main><h1>Welcome</h1><p>This is a perfectly ordinary "
        f"marketing page with more than two hundred characters of body copy "
        f"so that it clears the thin-content threshold used by the fetch "
        f"layer, unique per test call so extraction is a real cache miss "
        f"every time this test runs. (ref-{marker})</p></main></body></html>"
    ).encode()


def brief_response(*, category: str) -> httpx.Response:
    return chat_response(
        {
            "category": category,
            "segment": "B2B, freelancers and micro SMB",
            "geography": "global",
            "monetisation_guess": "seat based SaaS",
            "keywords": [category],
            "field_confidence": {"segment": 0.8, "geography": 0.8},
        }
    )


def plan_response(*, good_domain: str) -> httpx.Response:
    node = {
        "id": "t1",
        "kind": "discover_competitors",
        "budget_weight": 10,
        "query_variants": [good_domain],
        "keywords": None,
        "venues": None,
        "max_profile_count": 1,
        "consider_oss": False,
        "consider_funding": False,
    }
    return chat_response({"nodes": [node], "edges": [], "total_budget_weight": 10})


async def test_run_query_returns_a_persisted_report(pg_pool: asyncpg.Pool) -> None:
    good_domain = unique_domain()
    bad_domain = unique_domain()
    category = f"expense tracker {uuid.uuid4().hex[:8]}"

    transport = HostRoutedTransport()
    transport.add(
        "openrouter.ai",
        "/api/v1/chat/completions",
        brief_response(category=category),
        plan_response(good_domain=good_domain),
        extraction_response([]),  # homepage
        extraction_response(
            [
                {
                    "attribute": "pricing.entry_usd_month",
                    "value_num": 29,
                    "unit": "usd/month",
                    "quote": "$29/mo per seat",
                },
                {
                    "attribute": "pricing.model",
                    "value_text": "seat",
                    "quote": "$29/mo per seat",
                },
                {
                    "attribute": "pricing.free_tier",
                    "value_text": "true",
                    "quote": "A free tier is also available",
                },
            ]
        ),  # pricing page
    )
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
    transport.add(good_domain, "/", html_response(homepage_html(uuid.uuid4().hex[:8])))
    transport.add(good_domain, "/pricing", html_response(pricing_html(uuid.uuid4().hex[:8])))
    # bad_domain's "/" is unscripted -> 404 -> verification fails, dropped.

    http = httpx.AsyncClient(transport=httpx.MockTransport(transport.handler))
    settings = Settings()  # type: ignore[call-arg]

    outcome = await run_query(pg_pool, http, settings, category, no_cache=False, is_benchmark=True)
    await http.aclose()

    assert isinstance(outcome, RunOutcome)
    assert outcome.status == "done"
    assert outcome.run_id.startswith("r_")
    assert outcome.duration_s > 0
    assert outcome.coverage >= 0.0
    assert outcome.used_fallback is False  # a real scripted plan was used, not the safety net
    assert outcome.stats.claims_bound >= 1

    # The report handed back is exactly what got persisted, not a
    # separately-recomputed copy.
    row = await pg_pool.fetchrow(
        "SELECT status, is_benchmark, is_public, cost_usd, coverage FROM runs WHERE id = $1",
        outcome.run_id,
    )
    assert row is not None
    assert row["status"] == "done"
    assert row["is_benchmark"] is True
    assert row["is_public"] is False  # never auto-set — a deliberate, separate step
    assert float(row["cost_usd"]) == pytest.approx(outcome.cost_usd)
    assert float(row["coverage"]) == pytest.approx(outcome.coverage)

    persisted = await pg_pool.fetchval(
        "SELECT payload FROM reports WHERE run_id = $1", outcome.run_id
    )
    assert persisted is not None
    persisted_payload = json.loads(persisted) if isinstance(persisted, str) else persisted
    assert persisted_payload["run_id"] == outcome.report.run_id
    assert len(outcome.report.competitors) >= 1
