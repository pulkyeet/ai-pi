"""The injection boundary / anti-generic-advice guard (masterplan §4.9):
`api.synth.generate`'s three prompts receive only the resolved finding
set — never raw page text, quotes, or URLs. Asserted here by inspecting the
literal rendered prompt sent to the vendor, not just by reading the code
that builds it — the phase doc's own "asserted by inspecting the rendered
prompt" test spec."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from _db import insert_run

from api.llm.gateway import build_context
from api.synth.findings import Finding, FindingKind
from api.synth.generate import generate_feature_gaps, generate_mvp, generate_risks

pytestmark = pytest.mark.usefixtures("skip_without_postgres")

# A page-text-shaped marker: if this (or any URL) ever leaked into the
# rendered prompt, extraction's own untrusted page text would be reaching a
# free-text generation prompt — exactly the injection surface masterplan
# §8.3 says structurally cannot exist for this pipeline.
FORBIDDEN_MARKER = "RAW_PAGE_TEXT_MUST_NEVER_APPEAR_$49.99_https://vendor.example.com/pricing"


def _findings() -> list[Finding]:
    """`api.llm.cache`'s response cache is deliberately keyed without
    `run_id` (masterplan §9: a repeated call is nearly free), so a literal,
    identical findings block across two test functions would silently
    replay an earlier test's cached response instead of ever reaching this
    test's own scripted handler — the same lesson every prior phase's
    integration suite already hit (docs/working_knowledge.md). A `uuid4`
    token in each statement keeps every test's rendered prompt unique."""
    tag = uuid.uuid4().hex[:8]
    return [
        Finding(
            id=1,
            run_id="r1",
            kind=FindingKind.PAIN_POINT,
            statement=f"5 users across 3 threads report manual-entry-{tag}",
            claim_ids=[10, 11],
            support_count=5,
            confidence=0.5,
        ),
        Finding(
            id=2,
            run_id="r1",
            kind=FindingKind.COMPETITOR,
            statement=f"Acme-{tag} verified with 3 bound claims",
            claim_ids=[20, 21, 22],
            support_count=3,
        ),
        Finding(
            id=3,
            run_id="r1",
            kind=FindingKind.PRICING_OBSERVATION,
            statement=f"Entry pricing observed across 2 competitors-{tag}: median $10.00/mo",
            claim_ids=[30, 31],
            support_count=2,
        ),
    ]


def _chat_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        },
    )


def _capturing_handler(captured: dict[str, object], payload: dict):  # type: ignore[no-untyped-def]
    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _chat_response(payload)

    return handler


async def _ctx(pg_pool, run_id: str, handler):  # type: ignore[no-untyped-def]
    return build_context(
        pool=pg_pool,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_key="test-key",
        model="deepseek/deepseek-v4-flash",
        run_id=run_id,
    )


def _assert_no_leaked_page_text(body: object) -> None:
    assert isinstance(body, dict)
    rendered = json.dumps(body)
    assert FORBIDDEN_MARKER not in rendered
    assert "http://" not in rendered
    assert "https://" not in rendered


async def test_mvp_prompt_receives_only_findings_no_page_text(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    captured: dict[str, object] = {}
    payload = {"statement": "Grounded statement [1, 2, 3].", "addresses_finding_ids": [1, 2, 3]}
    ctx = await _ctx(pg_pool, run_id, _capturing_handler(captured, payload))

    result = await generate_mvp(_findings(), ctx=ctx)

    assert result is not None
    _assert_no_leaked_page_text(captured["body"])
    body = captured["body"]
    assert isinstance(body, dict)
    rendered_user_message = body["messages"][-1]["content"]
    assert "[1] kind=pain_point" in rendered_user_message
    assert "[2] kind=competitor" in rendered_user_message


async def test_feature_gaps_prompt_receives_only_findings_no_page_text(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    captured: dict[str, object] = {}
    payload = {"gaps": [{"statement": "Gap [1, 2, 3].", "addresses_finding_ids": [1, 2, 3]}]}
    ctx = await _ctx(pg_pool, run_id, _capturing_handler(captured, payload))

    result = await generate_feature_gaps(_findings(), ctx=ctx)

    assert result is not None
    _assert_no_leaked_page_text(captured["body"])


async def test_risks_prompt_receives_only_findings_no_page_text(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    captured: dict[str, object] = {}
    payload = {"risks": [{"statement": "Risk [1, 2, 3].", "addresses_finding_ids": [1, 2, 3]}]}
    ctx = await _ctx(pg_pool, run_id, _capturing_handler(captured, payload))

    result = await generate_risks(_findings(), ctx=ctx)

    assert result is not None
    _assert_no_leaked_page_text(captured["body"])


async def test_repair_round_names_the_specific_violation(pg_pool) -> None:
    """Rejection handling: a domain-invalid first response gets exactly one
    repair attempt naming what was wrong, then either succeeds or the
    section is omitted — never emitted with a loosened check."""
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    calls: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            # violates: cites only one finding, and it isn't a pain_point
            return _chat_response({"statement": "Bad.", "addresses_finding_ids": [2]})
        return _chat_response(
            {"statement": "Fixed statement [1, 2, 3].", "addresses_finding_ids": [1, 2, 3]}
        )

    ctx = await _ctx(pg_pool, run_id, handler)

    result = await generate_mvp(_findings(), ctx=ctx)

    assert result is not None
    assert result.statement == "Fixed statement [1, 2, 3]."
    assert len(calls) == 2
    repair_user_message = calls[1]["messages"][-1]["content"]
    assert "rejected" in repair_user_message.lower()


async def test_repeated_violation_omits_the_section(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)

    async def always_bad(request: httpx.Request) -> httpx.Response:
        return _chat_response({"statement": "Still bad.", "addresses_finding_ids": [2]})

    ctx = await _ctx(pg_pool, run_id, always_bad)

    result = await generate_mvp(_findings(), ctx=ctx)

    assert result is None


async def test_schema_invalid_response_on_both_attempts_omits_the_section(pg_pool) -> None:
    """A response that fails schema validation twice (not just domain
    validation) is `structured()`'s own `LLMValidationError` — the section
    is still omitted, not raised out of `generate_mvp`."""
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)

    async def malformed(request: httpx.Request) -> httpx.Response:
        return _chat_response({"not_a_statement_field": "oops"})

    ctx = await _ctx(pg_pool, run_id, malformed)

    result = await generate_mvp(_findings(), ctx=ctx)

    assert result is None
