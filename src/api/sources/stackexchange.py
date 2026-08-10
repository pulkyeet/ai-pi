"""Stack Exchange retriever (masterplan §5) — developer pain points, grade D.
Free anonymous quota (300/day, per Phase 01), no key required.

Quota is reported in the **response body** (`quota_max`/`quota_remaining`),
never in HTTP headers — a real deviation from the masterplan's own stated
assumption, confirmed in Phase 01 (`docs/external_apis.md`,
`docs/working_knowledge.md` Known Issues). This retriever polls the body and
raises `RetrieverUnavailableError` once `quota_remaining` hits zero, rather
than firing a request that's certain to be rejected.
"""

from __future__ import annotations

import asyncpg
import httpx
from pydantic import BaseModel

from api.sources.base import RetrieverUnavailableError
from api.sources.cache import cache_key, get_fresh, upsert
from api.sources.ratelimit import TokenBucket

BASE = "https://api.stackexchange.com/2.3"
RATE_PER_S = 1.0


class StackExchangeQuestion(BaseModel):
    title: str
    link: str
    body: str | None = None
    score: int | None = None


class StackExchangeRetriever:
    name = "stackexchange"
    grade = "D"

    def __init__(self, client: httpx.AsyncClient, *, pool: asyncpg.Pool | None = None) -> None:
        self._client = client
        self._pool = pool
        self._limiter = TokenBucket(RATE_PER_S, capacity=1)
        self._quota_remaining: int | None = None

    @property
    def quota_remaining(self) -> int | None:
        return self._quota_remaining

    async def search(
        self, query: str, *, site: str = "stackoverflow", limit: int = 5
    ) -> list[StackExchangeQuestion]:
        key = cache_key(self.name, query, site, str(limit))
        if self._pool is not None:
            cached = await get_fresh(self._pool, key)
            if cached is not None:
                return [StackExchangeQuestion.model_validate(item) for item in cached]
        if self._quota_remaining is not None and self._quota_remaining <= 0:
            raise RetrieverUnavailableError(self.name, "anonymous daily quota exhausted")

        await self._limiter.acquire()
        resp = await self._client.get(
            f"{BASE}/search/advanced",
            params={
                "order": "desc",
                "sort": "relevance",
                "q": query,
                "site": site,
                "filter": "withbody",
                "pagesize": limit,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        self._quota_remaining = body.get("quota_remaining")
        questions = [
            StackExchangeQuestion(
                title=item.get("title", ""),
                link=item.get("link", ""),
                body=item.get("body"),
                score=item.get("score"),
            )
            for item in body.get("items", [])
        ]
        if self._pool is not None:
            await upsert(
                self._pool,
                key=key,
                provider=self.name,
                payload=[question.model_dump(mode="json") for question in questions],
            )
        return questions


__all__ = ["StackExchangeQuestion", "StackExchangeRetriever"]
