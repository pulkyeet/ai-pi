"""Stage 0 (`api.planner.interpret.interpret`) against a scripted
OpenRouter transport plus real Postgres — deterministic offline, per the
phase doc's own testing table ("Fixture queries -> expected briefs, from
committed LLM responses. Stage 0 deterministic offline").

Every query is `uuid4`-suffixed, never a literal repeated across tests:
`api.llm.cache`'s response cache is deliberately permanent in this
long-lived Postgres container (masterplan §9, "a repeated call is nearly
free"), so two tests sharing one literal query text would collide on the
same cache row and the second test would silently observe the first test's
cached response instead of its own scripted one — the exact Phase 04/05
lesson recorded in `docs/working_knowledge.md`.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from _db import insert_run
from _http import ScriptedTransport, make_client

from api.llm.gateway import build_context
from api.planner.interpret import QueryRejectedError, RejectionReason, interpret

pytestmark = pytest.mark.usefixtures("skip_without_postgres")

CHAT_PATH = "/api/v1/chat/completions"
MODEL = "deepseek/deepseek-v4-flash"


def unique_query(base: str) -> str:
    return f"{base} (ref {uuid.uuid4().hex[:8]})"


def _chat_response(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {
                "prompt_tokens": 150,
                "completion_tokens": 60,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        },
    )


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


def _brief_payload(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "category": "expense management",
        "segment": "B2B, freelancers and micro SMB",
        "geography": "global",
        "monetisation_guess": "seat based SaaS",
        "keywords": ["expense tracking", "receipt scanning", "freelancer accounting"],
        "field_confidence": {
            "category": 0.9,
            "segment": 0.55,
            "geography": 0.4,
            "monetisation_guess": 0.7,
        },
    }
    defaults.update(overrides)
    return defaults


async def test_interpret_returns_typed_brief_and_keywords(pg_pool) -> None:
    payload = _brief_payload()
    transport = ScriptedTransport({CHAT_PATH: [_chat_response(payload)]})
    ctx = await _make_ctx(pg_pool, transport)

    result = await interpret(unique_query("AI expense tracker for freelancers"), ctx=ctx)

    assert transport.calls[CHAT_PATH] == 1
    assert result.brief.category == "expense management"
    assert result.brief.segment == "B2B, freelancers and micro SMB"
    assert result.brief.field_confidence == payload["field_confidence"]
    assert result.keywords == payload["keywords"]


async def test_interpret_computes_disambiguation_from_returned_confidence(pg_pool) -> None:
    payload = _brief_payload()
    transport = ScriptedTransport({CHAT_PATH: [_chat_response(payload)]})
    ctx = await _make_ctx(pg_pool, transport)

    result = await interpret(unique_query("AI expense tracker for freelancers"), ctx=ctx)

    # segment (0.55) is below the low-confidence threshold and plan-changing;
    # monetisation_guess (0.7) is above threshold; category (0.9) is high
    # confidence; geography (0.4) is low confidence and plan-changing.
    assert set(result.disambiguation_fields) == {"segment", "geography"}


async def test_rejected_query_never_calls_the_model(pg_pool) -> None:
    transport = ScriptedTransport({CHAT_PATH: [_chat_response(_brief_payload())]})
    ctx = await _make_ctx(pg_pool, transport)

    with pytest.raises(QueryRejectedError) as exc_info:
        await interpret("x" * 301, ctx=ctx)

    assert exc_info.value.reason is RejectionReason.TOO_LONG
    assert transport.calls[CHAT_PATH] == 0


async def test_out_of_range_confidence_is_clamped(pg_pool) -> None:
    payload = _brief_payload(field_confidence={"category": 1.5, "segment": -0.2})
    transport = ScriptedTransport({CHAT_PATH: [_chat_response(payload)]})
    ctx = await _make_ctx(pg_pool, transport)

    result = await interpret(unique_query("AI expense tracker for freelancers"), ctx=ctx)

    assert result.brief.field_confidence == {"category": 1.0, "segment": 0.0}


async def test_thin_category_produces_a_brief_not_a_rejection(pg_pool) -> None:
    """masterplan §10's degenerate-category worked example: a thin, unusual
    idea still produces a real brief rather than being rejected outright."""
    payload = _brief_payload(
        category="MEV monitoring",
        segment="solo validators",
        monetisation_guess="usage based",
        keywords=["MEV", "validator monitoring", "block builder"],
        field_confidence={"category": 0.4, "segment": 0.3, "geography": 0.6},
    )
    transport = ScriptedTransport({CHAT_PATH: [_chat_response(payload)]})
    ctx = await _make_ctx(pg_pool, transport)

    result = await interpret(unique_query("MEV monitoring for solo validators"), ctx=ctx)

    assert result.brief.category == "MEV monitoring"
    assert result.keywords
