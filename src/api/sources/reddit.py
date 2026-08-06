"""Reddit retriever (masterplan §13, D5 in `docs/execution_phases/README.md`)
— off by default. Self-service OAuth app registration is closed; new
credentials need 2-4 weeks of manual approval, so this ships behind
`ENABLE_REDDIT` (default `False`). A run degrades to a coverage gap rather
than blocking on Reddit: HN Algolia + GitHub + Stack Exchange are the
community-mining backbone regardless (see `docs/tracker.md`).

If credentials do arrive, masterplan §13's constraints apply strictly:
search-based access only, short spans, link out, never mirror — reflected
here by only ever calling the public search endpoint, never a bulk listing.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from api.sources.base import RetrieverUnavailableError
from api.sources.ratelimit import TokenBucket

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
SEARCH_URL = "https://oauth.reddit.com/search"
USER_AGENT = "AIProductInvestigatorBot/0.1 (+mailto:pulkyeet@gmail.com)"
RATE_PER_S = 1.0  # well under the 100 QPM free-tier ceiling


class RedditPost(BaseModel):
    title: str
    permalink: str
    score: int | None = None


class RedditRetriever:
    name = "reddit"
    grade = "D"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        enabled: bool,
        client_id: str | None,
        client_secret: str | None,
    ) -> None:
        self._client = client
        self._enabled = enabled
        self._client_id = client_id
        self._client_secret = client_secret
        self._limiter = TokenBucket(RATE_PER_S, capacity=1)

    async def search(self, query: str, *, limit: int = 10) -> list[RedditPost]:
        if not self._enabled:
            raise RetrieverUnavailableError(self.name, "ENABLE_REDDIT is off")
        if not self._client_id or not self._client_secret:
            raise RetrieverUnavailableError(self.name, "no OAuth credentials configured")

        await self._limiter.acquire()
        token = await self._access_token()
        resp = await self._client.get(
            SEARCH_URL,
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
            params={"q": query, "limit": limit, "sort": "relevance"},
        )
        resp.raise_for_status()
        body = resp.json()
        return [
            RedditPost(
                title=child["data"].get("title", ""),
                permalink=child["data"].get("permalink", ""),
                score=child["data"].get("score"),
            )
            for child in body.get("data", {}).get("children", [])
        ]

    async def _access_token(self) -> str:
        assert self._client_id and self._client_secret
        resp = await self._client.post(
            TOKEN_URL,
            auth=(self._client_id, self._client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        token: str = resp.json()["access_token"]
        return token


__all__ = ["RedditPost", "RedditRetriever"]
