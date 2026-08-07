"""`LLMClient`'s own HTTP mechanics (retries, backoff, error classification)
against a scripted transport — no real vendor involved, same split Phase 03/
04 drew between their own HTTP mechanics tests and Phase 01's vendor
cassettes. See `tests/integration/_http.py`'s docstring.
"""

from __future__ import annotations

import json

import httpx
from _http import ScriptedTransport, make_client

from api.llm.client import LLMClient, LLMProviderError

CHAT_PATH = "/api/v1/chat/completions"
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "test", "strict": True, "schema": {"type": "object"}},
}


def _ok_response(content: dict[str, str] | str = "{}") -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": text}}],
            "usage": {
                "prompt_tokens": 42,
                "completion_tokens": 7,
                "prompt_tokens_details": {"cached_tokens": 3},
            },
        },
    )


async def test_success_parses_content_and_usage() -> None:
    transport = ScriptedTransport({CHAT_PATH: [_ok_response({"a": 1})]})
    async with make_client(transport) as client:
        llm_client = LLMClient(client, api_key="test-key", model="deepseek/deepseek-v4-flash")
        raw = await llm_client.complete(
            [{"role": "user", "content": "hi"}], response_format=RESPONSE_FORMAT
        )

    assert raw.content == '{"a": 1}'
    assert raw.input_tokens == 42
    assert raw.output_tokens == 7
    assert raw.cached_tokens == 3


async def test_429_then_success_retries_and_returns() -> None:
    transport = ScriptedTransport({CHAT_PATH: [httpx.Response(429), _ok_response()]})
    async with make_client(transport) as client:
        llm_client = LLMClient(client, api_key="test-key", model="deepseek/deepseek-v4-flash")
        raw = await llm_client.complete(
            [{"role": "user", "content": "hi"}], response_format=RESPONSE_FORMAT
        )

    assert raw.content == "{}"
    assert transport.calls[CHAT_PATH] == 2


async def test_5xx_exhausts_retries_and_raises_typed_error() -> None:
    transport = ScriptedTransport({CHAT_PATH: [httpx.Response(503)]})
    async with make_client(transport) as client:
        llm_client = LLMClient(client, api_key="test-key", model="deepseek/deepseek-v4-flash")
        try:
            await llm_client.complete(
                [{"role": "user", "content": "hi"}], response_format=RESPONSE_FORMAT
            )
            raise AssertionError("expected LLMProviderError")
        except LLMProviderError:
            pass

    assert transport.calls[CHAT_PATH] == 3  # MAX_ATTEMPTS from api.executor.retry


async def test_non_retryable_status_raises_immediately() -> None:
    transport = ScriptedTransport({CHAT_PATH: [httpx.Response(400)]})
    async with make_client(transport) as client:
        llm_client = LLMClient(client, api_key="test-key", model="deepseek/deepseek-v4-flash")
        try:
            await llm_client.complete(
                [{"role": "user", "content": "hi"}], response_format=RESPONSE_FORMAT
            )
            raise AssertionError("expected LLMProviderError")
        except LLMProviderError:
            pass

    assert transport.calls[CHAT_PATH] == 1  # no retry budget spent on a non-retryable failure


async def test_all_attempts_timeout_raises_typed_error() -> None:
    transport = ScriptedTransport(
        {
            CHAT_PATH: [
                httpx.TimeoutException("boom"),
                httpx.TimeoutException("boom"),
                httpx.TimeoutException("boom"),
            ]
        }
    )
    async with make_client(transport) as client:
        llm_client = LLMClient(client, api_key="test-key", model="deepseek/deepseek-v4-flash")
        try:
            await llm_client.complete(
                [{"role": "user", "content": "hi"}], response_format=RESPONSE_FORMAT
            )
            raise AssertionError("expected LLMProviderError")
        except LLMProviderError:
            pass

    assert transport.calls[CHAT_PATH] == 3


async def test_timeout_then_success_retries() -> None:
    transport = ScriptedTransport({CHAT_PATH: [httpx.TimeoutException("boom"), _ok_response()]})
    async with make_client(transport) as client:
        llm_client = LLMClient(client, api_key="test-key", model="deepseek/deepseek-v4-flash")
        raw = await llm_client.complete(
            [{"role": "user", "content": "hi"}], response_format=RESPONSE_FORMAT
        )

    assert raw.content == "{}"
    assert transport.calls[CHAT_PATH] == 2


async def test_provider_pinning_and_temperature_are_always_sent() -> None:
    """Provider pinning (`require_parameters`) and `temperature: 0` are
    non-negotiable per Phase 01's measured findings — assert the actual
    request body, not just the parsed response."""
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ok_response()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm_client = LLMClient(client, api_key="test-key", model="deepseek/deepseek-v4-flash")
        await llm_client.complete(
            [{"role": "user", "content": "hi"}], response_format=RESPONSE_FORMAT
        )

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["provider"] == {"require_parameters": True}
    assert body["temperature"] == 0
