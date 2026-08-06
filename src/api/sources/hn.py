"""HN Algolia retriever (masterplan §5) — no auth, launches/comments/post
volume as a free trend signal alongside community pain points. Grade D
(comments), per masterplan §4.6.

No rate limit observed in Phase 01 (`docs/external_apis.md`: "no
rate-limit headers present at all"), but a gentle self-imposed limiter is
declared anyway per the phase doc's own rule that every retriever declares
one — unlimited is not the same as "hit it as fast as possible".
"""

from __future__ import annotations

from datetime import datetime

import httpx
from pydantic import BaseModel

from api.sources.ratelimit import TokenBucket

BASE = "https://hn.algolia.com/api/v1"
RATE_PER_S = 5.0


class HNHit(BaseModel):
    title: str
    url: str | None = None
    points: int | None = None
    num_comments: int | None = None
    created_at: datetime | None = None


class HNRetriever:
    name = "hn_algolia"
    grade = "D"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._limiter = TokenBucket(RATE_PER_S)

    async def search(self, query: str, *, by_date: bool = False) -> list[HNHit]:
        await self._limiter.acquire()
        endpoint = "search_by_date" if by_date else "search"
        params: dict[str, str] = {"query": query}
        if by_date:
            params["tags"] = "story"
        resp = await self._client.get(f"{BASE}/{endpoint}", params=params)
        resp.raise_for_status()
        body = resp.json()
        return [
            HNHit(
                title=hit.get("title") or "",
                url=hit.get("url"),
                points=hit.get("points"),
                num_comments=hit.get("num_comments"),
                created_at=hit.get("created_at"),
            )
            for hit in body.get("hits", [])
        ]


__all__ = ["HNHit", "HNRetriever"]
