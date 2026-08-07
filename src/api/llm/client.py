"""OpenRouter transport for `api.llm.gateway.structured()` (masterplan §6,
Phase 05). The only module that speaks HTTP to OpenRouter — `api.llm.gateway`
calls it at most twice per `structured()` call (the initial attempt and, if
validation fails, exactly one repair retry). Nothing outside `api.llm` may
import this module directly — see the `TID251` rule in `pyproject.toml` and
this phase's exit criterion that `structured()` is the only way any module
calls a model.

Provider pinning (`provider.require_parameters: true`) and `temperature: 0`
are applied unconditionally here, never left to a caller to remember — Phase
01 measured the schema-violation rate and latency/output-length variance
with and without both (docs/external_apis.md); this is where that finding
becomes enforced behaviour rather than a documented recommendation.

Retry reuses Phase 02's policy (`api.executor.retry`) unmodified, exactly
like `api.search.exa.ExaProvider`: 429/5xx/timeout only, full-jitter
backoff, `LLMProviderError` after retries are exhausted.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel

from api.executor.retry import MAX_ATTEMPTS, RETRYABLE_STATUS_CODES, backoff_delay

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
TOTAL_TIMEOUT_S = 60.0


class RawCompletion(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int


class LLMProviderError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(f"openrouter: {message}")


def combine_usage(first: RawCompletion, second: RawCompletion) -> RawCompletion:
    """Total token usage across an initial attempt and its one repair
    retry — both are real spend against the vendor, so cost accounting must
    not silently drop the first call just because its output was discarded.
    `content` is the second (repaired) call's, since that's the one that
    validated."""
    return RawCompletion(
        content=second.content,
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        cached_tokens=first.cached_tokens + second.cached_tokens,
    )


def _parse_completion(body: dict[str, Any]) -> RawCompletion:
    content = body["choices"][0]["message"]["content"]
    usage = body.get("usage", {}) or {}
    cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
    return RawCompletion(
        content=content,
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        cached_tokens=cached,
    )


class LLMClient:
    provider = "openrouter"

    def __init__(self, client: httpx.AsyncClient, api_key: str, model: str) -> None:
        self._client = client
        self._api_key = api_key
        self.model = model

    async def complete(
        self, messages: list[dict[str, str]], *, response_format: dict[str, Any]
    ) -> RawCompletion:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "response_format": response_format,
            "provider": {"require_parameters": True},
            "temperature": 0,
            "usage": {"include": True},
        }
        response_body = await self._post_with_retries(body)
        return _parse_completion(response_body)

    async def _post_with_retries(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        last_error: Exception | None = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = await asyncio.wait_for(
                    self._client.post(CHAT_URL, headers=headers, json=body),
                    timeout=TOTAL_TIMEOUT_S,
                )
            except (httpx.TimeoutException, TimeoutError) as exc:
                last_error = exc
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(backoff_delay(attempt))
                continue

            if resp.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(backoff_delay(attempt))
                continue

            if resp.status_code != 200:
                raise LLMProviderError(f"HTTP {resp.status_code}")

            result: dict[str, Any] = resp.json()
            return result

        raise LLMProviderError("retries exhausted") from last_error


__all__ = ["CHAT_URL", "LLMClient", "LLMProviderError", "RawCompletion", "combine_usage"]
