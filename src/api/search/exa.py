"""Exa search provider (masterplan §7, D1/D2 in docs/execution_phases/README.md).

The only `SearchProvider` in v1 — Brave and Google CSE are both out (see the
phase doc). $0.007/query flat across search modes, reported back per-call in
`costDollars.total`; `contents.text` adds real page text so downstream
callers (SERP-snippet reading, in particular) have something to quote.

Retry reuses Phase 02's policy (`api.executor.retry`) unmodified, exactly
like `api.retrieval.fetch` does: 429/5xx/timeout only, full-jitter backoff.
After retries are exhausted, this raises `ProviderError` — `api.search.router`
is the only caller expected to catch it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from api.executor.retry import MAX_ATTEMPTS, RETRYABLE_STATUS_CODES, backoff_delay
from api.search.base import ProviderError, SearchResponse, SearchResult

logger = structlog.get_logger()

BASE_URL = "https://api.exa.ai/search"
TOTAL_TIMEOUT_S = 15.0
SNIPPET_MAX_CHARS = 500


def _build_payload(
    query: str, *, limit: int, site: str | None, mode: str, include_contents: bool
) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": query, "numResults": limit}
    if mode:
        payload["type"] = mode
    if include_contents:
        payload["contents"] = {"text": True}
    if site:
        payload["includeDomains"] = [site]
    return payload


def _to_snippet(text: str | None) -> str:
    if not text:
        return ""
    text = text.strip()
    return text[:SNIPPET_MAX_CHARS]


def _parse_response(body: dict[str, Any]) -> tuple[list[SearchResult], float]:
    results = [
        SearchResult(
            url=item.get("url", ""),
            title=item.get("title", ""),
            snippet=_to_snippet(item.get("text")),
            rank=rank,
            provider="exa",
            raw=item,
        )
        for rank, item in enumerate(body.get("results", []))
    ]
    credits_usd = float(body.get("costDollars", {}).get("total", 0.0))
    return results, credits_usd


class ExaProvider:
    name = "exa"

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        *,
        mode: str = "neural",
        include_contents: bool = True,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._mode = mode
        self._include_contents = include_contents

    async def search(
        self, query: str, *, limit: int = 10, site: str | None = None
    ) -> SearchResponse:
        payload = _build_payload(
            query,
            limit=limit,
            site=site,
            mode=self._mode,
            include_contents=self._include_contents,
        )
        body = await self._post_with_retries(payload)
        results, credits_usd = _parse_response(body)
        return SearchResponse(results=results, credits_usd=credits_usd, provider=self.name)

    async def _post_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"x-api-key": self._api_key, "content-type": "application/json"}
        last_error: Exception | None = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = await asyncio.wait_for(
                    self._client.post(BASE_URL, headers=headers, json=payload),
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
                raise ProviderError(self.name, f"HTTP {resp.status_code}")

            result: dict[str, Any] = resp.json()
            return result

        raise ProviderError(self.name, "retries exhausted") from last_error


__all__ = ["ExaProvider"]
