"""`bench.runner` (Phase 14), Postgres-gated: `run_and_score` drives the
real pipeline (the same scripted-transport pattern as
`test_cli_run_query.py`) and scores the result against a `BenchmarkQuery`'s
ground truth; `write_results` persists that score as a real, readable JSON
snapshot.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

import asyncpg
import httpx
import pytest
from _tasks_http import HostRoutedTransport, chat_response, exa_response, extraction_response
from bench.loader import BenchmarkQuery, GroundTruth
from bench.runner import run_and_score, write_results

from api.config import Settings

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


def unique_domain() -> str:
    return f"t{uuid.uuid4().hex[:12]}.com"


def html_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "text/html"})


def pricing_html(marker: str) -> bytes:
    return (
        f"<html><body><main><h1>Pricing</h1><p>Pro plan: $29/mo per seat. "
        f"A free tier is also available for small teams. (ref-{marker})</p>"
        f"</main></body></html>"
    ).encode()


def homepage_html(marker: str) -> bytes:
    return (
        f"<html><body><main><h1>Welcome</h1><p>This is a perfectly ordinary "
        f"marketing page with more than two hundred characters of body copy "
        f"so that it clears the thin-content threshold used by the fetch "
        f"layer. (ref-{marker})</p></main></body></html>"
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


async def test_run_and_score_scores_a_query_against_its_ground_truth(
    pg_pool: asyncpg.Pool,
) -> None:
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
                    "quote": "$29/mo per seat",
                },
                {"attribute": "pricing.model", "value_text": "seat", "quote": "$29/mo per seat"},
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

    http = httpx.AsyncClient(transport=httpx.MockTransport(transport.handler))
    settings = Settings()  # type: ignore[call-arg]

    query = BenchmarkQuery(
        id="q99",
        query=category,
        difficulty="easy",
        split="tuning",
        ground_truth=GroundTruth(
            must_include=[good_domain],
            known_absent=[bad_domain],
            facts=[
                {
                    "entity": good_domain,
                    "attribute": "pricing.entry_usd_month",
                    "value": 29,
                    "verified_on": date.today().isoformat(),
                }
            ],
        ),
    )

    score = await run_and_score(pg_pool, http, settings, query)
    await http.aclose()

    assert score.query_id == "q99"
    assert score.run_id.startswith("r_")
    # bad_domain never verifies (its "/" is unscripted -> 404), so it can
    # never appear in the report — recall/precision are both perfect here.
    assert score.competitor_recall == 1.0
    assert score.precision_proxy == 1.0
    assert score.fact_accuracy == 1.0
    assert score.sentence_binding_rate >= 0.0
    assert score.contradiction_fired is False
    assert score.cost_usd > 0
    assert score.used_fallback is False
    assert score.difficulty == "easy"
    assert score.split == "tuning"


def test_write_results_produces_readable_dated_json(tmp_path: Path) -> None:
    from bench.runner import QueryScore

    score = QueryScore(
        query_id="q01",
        query="project management tool",
        run_id="r_test123",
        difficulty="easy",
        split="tuning",
        competitor_recall=1.0,
        precision_proxy=1.0,
        fact_accuracy=1.0,
        sentence_binding_rate=1.0,
        contradiction_fired=False,
        cost_usd=0.01,
        llm_cost_usd=0.005,
        search_cost_usd=0.005,
        duration_s=10.0,
        coverage=0.9,
        used_fallback=False,
        claims_dropped={},
        synthesis_omitted_sections=[],
    )
    out_dir = write_results([score], results_dir=tmp_path, as_of=date(2026, 8, 8))
    assert out_dir == tmp_path / "2026-08-08"
    written = json.loads((out_dir / "q01.json").read_text())
    assert written["query_id"] == "q01"
    assert written["competitor_recall"] == 1.0
