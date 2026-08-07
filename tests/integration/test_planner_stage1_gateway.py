"""Stage 1 (`api.planner.plan.plan_stage1`) against a scripted OpenRouter
transport plus real Postgres — deterministic offline, per the phase doc's
own testing table ("Fixture briefs -> expected plans, structural
equality... Invalid plan -> repair -> valid... Invalid twice -> fallback
used, counted... Category sensitivity... Thin category... Budget
allocation sums correctly").

Every call passes `unique_keywords(...)` rather than a literal list: the
rendered `{{keywords}}` template variable feeds `api.llm.cache`'s response
cache key, which is deliberately permanent in this long-lived Postgres
container, so two tests sharing identical keywords would collide on the
same cache row (see `test_planner_stage0_gateway.py`'s module docstring
for the same lesson applied to Stage 0's query text).
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from _db import insert_run
from _http import ScriptedTransport, make_client

from api.llm.gateway import build_context
from api.models.brief import ResearchBrief
from api.models.plan import TaskKind
from api.planner.plan import plan_stage1
from api.planner.registry import DEFAULT_RUN_BUDGET_WEIGHT

pytestmark = pytest.mark.usefixtures("skip_without_postgres")

CHAT_PATH = "/api/v1/chat/completions"
MODEL = "deepseek/deepseek-v4-flash"

BRIEF = ResearchBrief(
    category="expense management",
    segment="B2B, freelancers and micro SMB",
    geography="global",
    monetisation_guess="seat based SaaS",
    field_confidence={},
)


def unique_keywords(*base: str) -> list[str]:
    return [*base, f"ref-{uuid.uuid4().hex[:8]}"]


def _chat_response(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 80,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        },
    )


def _plan_payload(
    nodes: list[dict[str, object]], *, edges: list[list[str]] | None = None
) -> dict[str, object]:
    total = sum(int(n["budget_weight"]) for n in nodes)  # type: ignore[arg-type]
    return {"nodes": nodes, "edges": edges or [], "total_budget_weight": total}


def _discover_node(
    *, id: str = "t1", budget_weight: int = 15, max_profile_count: int = 5
) -> dict[str, object]:
    return {
        "id": id,
        "kind": "discover_competitors",
        "budget_weight": budget_weight,
        "query_variants": ["expense tracker", "receipt scanning app"],
        "max_profile_count": max_profile_count,
        "consider_oss": False,
        "consider_funding": True,
    }


def _mine_node(*, id: str = "t2", budget_weight: int = 4) -> dict[str, object]:
    return {
        "id": id,
        "kind": "mine_community",
        "budget_weight": budget_weight,
        "keywords": ["expense tracker"],
        "venues": ["hn", "stackexchange"],
    }


async def _make_ctx(pg_pool, transport: ScriptedTransport):
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    return build_context(
        pool=pg_pool,
        http_client=make_client(transport),
        api_key="test-key",
        model=MODEL,
        run_id=run_id,
    )


async def test_valid_plan_used_directly_no_repair(pg_pool) -> None:
    payload = _plan_payload([_discover_node(), _mine_node()])
    transport = ScriptedTransport({CHAT_PATH: [_chat_response(payload)]})
    ctx = await _make_ctx(pg_pool, transport)

    outcome = await plan_stage1(BRIEF, unique_keywords("expense tracker"), ctx=ctx)

    assert outcome.used_fallback is False
    assert outcome.repaired is False
    assert transport.calls[CHAT_PATH] == 1
    kinds = {n.kind for n in outcome.plan.nodes}
    assert kinds == {TaskKind.DISCOVER_COMPETITORS, TaskKind.MINE_COMMUNITY}
    assert outcome.plan.total_budget_weight == 19


async def test_domain_invalid_plan_is_repaired(pg_pool) -> None:
    over_budget = _plan_payload([_discover_node(budget_weight=500)])
    valid = _plan_payload([_discover_node(), _mine_node()])
    transport = ScriptedTransport({CHAT_PATH: [_chat_response(over_budget), _chat_response(valid)]})
    ctx = await _make_ctx(pg_pool, transport)

    outcome = await plan_stage1(BRIEF, unique_keywords("expense tracker"), ctx=ctx)

    assert transport.calls[CHAT_PATH] == 2
    assert outcome.used_fallback is False
    assert outcome.repaired is True


async def test_repair_prompt_carries_the_specific_domain_error(pg_pool) -> None:
    missing_discovery = _plan_payload([_mine_node()])
    valid = _plan_payload([_discover_node(), _mine_node()])
    captured: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body["messages"][-1]["content"])
        return _chat_response(missing_discovery if len(captured) == 1 else valid)

    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    ctx = build_context(
        pool=pg_pool,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_key="test-key",
        model=MODEL,
        run_id=run_id,
    )

    await plan_stage1(BRIEF, unique_keywords("expense tracker"), ctx=ctx)

    assert len(captured) == 2
    assert "missing_discovery" in captured[1]


async def test_invalid_twice_falls_back_and_is_counted(pg_pool) -> None:
    missing_discovery = _plan_payload([_mine_node()])
    transport = ScriptedTransport(
        {CHAT_PATH: [_chat_response(missing_discovery), _chat_response(missing_discovery)]}
    )
    ctx = await _make_ctx(pg_pool, transport)

    outcome = await plan_stage1(BRIEF, unique_keywords("expense tracker"), ctx=ctx)

    assert transport.calls[CHAT_PATH] == 2
    assert outcome.used_fallback is True
    assert any(n.kind is TaskKind.DISCOVER_COMPETITORS for n in outcome.plan.nodes)


async def test_dev_tools_category_plan_differs_from_consumer_category_plan(pg_pool) -> None:
    """Proves the pipeline preserves a category-conditional planning
    decision end to end rather than collapsing every category onto one
    template — offline determinism means the *decision itself* here is
    scripted, not made live by the model (that is measured in Phase 14's
    benchmark harness), but the DAG shape genuinely differs based on it."""
    dev_tools_payload = _plan_payload(
        [
            {**_discover_node(), "consider_oss": True},
            {
                "id": "t2",
                "kind": "mine_community",
                "budget_weight": 4,
                "keywords": ["ci pipeline", "developer tooling"],
                "venues": ["hn", "github", "stackexchange"],
            },
        ]
    )
    consumer_payload = _plan_payload([{**_discover_node(), "consider_oss": False}])

    dev_transport = ScriptedTransport({CHAT_PATH: [_chat_response(dev_tools_payload)]})
    consumer_transport = ScriptedTransport({CHAT_PATH: [_chat_response(consumer_payload)]})
    dev_ctx = await _make_ctx(pg_pool, dev_transport)
    consumer_ctx = await _make_ctx(pg_pool, consumer_transport)

    dev_outcome = await plan_stage1(BRIEF, unique_keywords("ci pipeline"), ctx=dev_ctx)
    consumer_outcome = await plan_stage1(BRIEF, unique_keywords("mood tracker"), ctx=consumer_ctx)

    dev_discover = next(
        n for n in dev_outcome.plan.nodes if n.kind is TaskKind.DISCOVER_COMPETITORS
    )
    consumer_discover = next(
        n for n in consumer_outcome.plan.nodes if n.kind is TaskKind.DISCOVER_COMPETITORS
    )
    assert dev_discover.args["consider_oss"] is True
    assert consumer_discover.args["consider_oss"] is False
    dev_kinds = {n.kind for n in dev_outcome.plan.nodes}
    consumer_kinds = {n.kind for n in consumer_outcome.plan.nodes}
    assert dev_kinds != consumer_kinds


async def test_thin_category_produces_a_plan_not_empty(pg_pool) -> None:
    payload = _plan_payload([_discover_node(max_profile_count=2)])
    transport = ScriptedTransport({CHAT_PATH: [_chat_response(payload)]})
    ctx = await _make_ctx(pg_pool, transport)

    outcome = await plan_stage1(
        ResearchBrief(
            category="MEV monitoring",
            segment="solo validators",
            geography="global",
            monetisation_guess="usage based",
            field_confidence={},
        ),
        unique_keywords("MEV", "validator monitoring"),
        ctx=ctx,
    )

    assert outcome.plan.nodes
    assert outcome.used_fallback is False


async def test_budget_allocation_sums_correctly_and_respects_cap(pg_pool) -> None:
    payload = _plan_payload([_discover_node(budget_weight=20), _mine_node(budget_weight=10)])
    transport = ScriptedTransport({CHAT_PATH: [_chat_response(payload)]})
    ctx = await _make_ctx(pg_pool, transport)

    outcome = await plan_stage1(BRIEF, unique_keywords("expense tracker"), ctx=ctx)

    assert outcome.plan.total_budget_weight == sum(n.budget_weight for n in outcome.plan.nodes)
    assert outcome.plan.total_budget_weight <= DEFAULT_RUN_BUDGET_WEIGHT


async def test_over_run_budget_cap_falls_back_when_not_repaired(pg_pool) -> None:
    over_budget = _plan_payload([_discover_node(budget_weight=1000)])
    transport = ScriptedTransport(
        {CHAT_PATH: [_chat_response(over_budget), _chat_response(over_budget)]}
    )
    ctx = await _make_ctx(pg_pool, transport)

    outcome = await plan_stage1(BRIEF, unique_keywords("expense tracker"), ctx=ctx)

    assert outcome.used_fallback is True
    assert outcome.plan.total_budget_weight <= DEFAULT_RUN_BUDGET_WEIGHT
