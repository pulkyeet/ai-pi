"""End-to-end `structured()` against a scripted OpenRouter transport plus
real Postgres — proves the whole pipeline (client, prompts, cost, cache,
ledger, tracing) works together, not just each module in isolation (those
have their own dedicated test files).

Every test renders the `echo` prompt with a `uuid4`-suffixed `message`
variable, never a literal like `"hi"`: `llm_response_cache` is deliberately
keyed without `run_id` (masterplan §9's "a repeated call is nearly free"),
so two tests using the same literal variable collide on the same cache row
in this long-lived Postgres container — the exact Phase 04 lesson recorded
in docs/working_knowledge.md ("a test against a deliberately shared/
persistent cache or ledger table needs its own uniqueness strategy").
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from _db import insert_run
from _http import ScriptedTransport, make_client
from _llm_fixtures import FIXTURE_PROMPTS_DIR, EchoResult

from api.llm.gateway import LLMContext, LLMValidationError, build_context, structured

pytestmark = pytest.mark.usefixtures("skip_without_postgres")

CHAT_PATH = "/api/v1/chat/completions"


def unique_message() -> str:
    return f"hi-{uuid.uuid4().hex[:12]}"


def _response(content: dict[str, str]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(content)}}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        },
    )


def _malformed_response() -> httpx.Response:
    return _response({"not_message": "oops, wrong field entirely"})


def _make_ctx(
    pg_pool,
    transport: ScriptedTransport,
    run_id: str,
    *,
    langfuse_host: str | None = None,
) -> LLMContext:
    return build_context(
        pool=pg_pool,
        http_client=make_client(transport),
        api_key="test-key",
        model="deepseek/deepseek-v4-flash",
        run_id=run_id,
        prompts_dir=FIXTURE_PROMPTS_DIR,
        langfuse_public_key="pk-test" if langfuse_host else None,
        langfuse_secret_key="sk-test" if langfuse_host else None,
        langfuse_host=langfuse_host or "https://cloud.langfuse.com",
    )


async def test_happy_path_returns_validated_model_and_records_cost(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    transport = ScriptedTransport({CHAT_PATH: [_response({"message": "hello world"})]})
    ctx = _make_ctx(pg_pool, transport, run_id)

    result = await structured(EchoResult, "echo", {"message": unique_message()}, ctx=ctx)

    assert result.value.message == "hello world"
    assert result.cache_hit is False
    assert result.repaired is False
    assert result.cost_usd > 0

    row = await pg_pool.fetchrow(
        "SELECT cache_hit, repaired FROM llm_calls WHERE run_id = $1", run_id
    )
    assert row is not None
    assert row["cache_hit"] is False
    assert row["repaired"] is False


async def test_untrusted_content_reaches_the_model_only_as_a_delimited_block(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _response({"message": "ok"})

    ctx = build_context(
        pool=pg_pool,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_key="test-key",
        model="deepseek/deepseek-v4-flash",
        run_id=run_id,
        prompts_dir=FIXTURE_PROMPTS_DIR,
    )
    await structured(
        EchoResult,
        "echo",
        {"message": unique_message()},
        untrusted={"page_text": "<script>alert(1)</script>"},
        ctx=ctx,
    )

    body = captured["body"]
    assert isinstance(body, dict)
    user_content = body["messages"][-1]["content"]
    assert "<untrusted" in user_content
    assert "<script>alert(1)</script>" not in user_content
    assert "&lt;script&gt;" in user_content


async def test_malformed_then_valid_repairs_once(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    transport = ScriptedTransport(
        {CHAT_PATH: [_malformed_response(), _response({"message": "fixed"})]}
    )
    ctx = _make_ctx(pg_pool, transport, run_id)

    result = await structured(EchoResult, "echo", {"message": unique_message()}, ctx=ctx)

    assert result.value.message == "fixed"
    assert result.repaired is True
    assert transport.calls[CHAT_PATH] == 2


async def test_malformed_twice_raises_and_leaks_no_raw_payload(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    transport = ScriptedTransport({CHAT_PATH: [_malformed_response(), _malformed_response()]})
    ctx = _make_ctx(pg_pool, transport, run_id)

    with pytest.raises(LLMValidationError) as exc_info:
        await structured(EchoResult, "echo", {"message": unique_message()}, ctx=ctx)

    assert "not_message" not in str(exc_info.value)
    assert "oops" not in str(exc_info.value)

    row = await pg_pool.fetchrow(
        "SELECT cost_usd, repaired FROM llm_calls WHERE run_id = $1", run_id
    )
    assert row is not None
    assert float(row["cost_usd"]) > 0  # real spend still accounted, even on failure
    assert row["repaired"] is True


async def test_response_cache_avoids_a_second_network_call(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    transport = ScriptedTransport({CHAT_PATH: [_response({"message": "cached value"})]})
    ctx = _make_ctx(pg_pool, transport, run_id)
    message = unique_message()

    first = await structured(EchoResult, "echo", {"message": message}, ctx=ctx)
    second = await structured(EchoResult, "echo", {"message": message}, ctx=ctx)

    assert transport.calls[CHAT_PATH] == 1
    assert first.value == second.value
    assert second.cache_hit is True
    assert second.cost_usd == 0.0


async def test_langfuse_outage_does_not_fail_the_run(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    transport = ScriptedTransport({CHAT_PATH: [_response({"message": "still works"})]})
    ctx = _make_ctx(pg_pool, transport, run_id, langfuse_host="http://127.0.0.1:1")

    result = await structured(EchoResult, "echo", {"message": unique_message()}, ctx=ctx)

    assert result.value.message == "still works"
