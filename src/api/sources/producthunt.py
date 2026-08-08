"""Product Hunt retriever (masterplan §5) — launch date, tagline, upvotes.
Grade B. Free GraphQL developer token (obtained 2026-08-07, verified live —
`docs/external_apis.md`), with a real recorded cassette in
`tests/fixtures/cassettes/producthunt_post_by_slug.yaml`.

**No `search_posts`: Product Hunt v2 GraphQL has no text-search field.** Its
query root exposes only `collection`/`collections`/`comment`/`post`/`posts`/
`topic`/`topics`/`user`/`viewer` (verified via schema introspection, 2026-08-07) —
the original `posts(order: VOTES)` "search" query was invalid on arrival (it
declared an unused `$query` variable, so GraphQL rejected it with
`variableNotUsed` and it silently always returned `[]`). The retriever's only
real operation is the exact-slug lookup `post_by_slug`, which Phase 07's `ph:`
artifact verification (masterplan §4.5) needs: "does this exact post exist",
not "what posts match this text".

Raises `RetrieverUnavailableError` immediately (no network call) when
`producthunt_token` isn't configured, same as Reddit's optionality.
"""

from __future__ import annotations

from datetime import datetime

import httpx
from pydantic import BaseModel

from api.sources.base import RetrieverUnavailableError
from api.sources.ratelimit import TokenBucket

GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"
RATE_PER_S = 1.0

_POST_BY_SLUG_QUERY = """
query($slug: String!) {
  post(slug: $slug) {
    name
    tagline
    votesCount
    website
    featuredAt
  }
}
"""


class ProductHuntPost(BaseModel):
    name: str
    tagline: str
    votes_count: int
    website: str | None = None
    featured_at: datetime | None = None


class ProductHuntRetriever:
    name = "producthunt"
    grade = "B"

    def __init__(self, client: httpx.AsyncClient, token: str | None) -> None:
        self._client = client
        self._token = token
        self._limiter = TokenBucket(RATE_PER_S)

    async def post_by_slug(self, slug: str) -> ProductHuntPost | None:
        if not self._token:
            raise RetrieverUnavailableError(
                self.name, "no developer token configured (registration pending)"
            )
        await self._limiter.acquire()
        resp = await self._client.post(
            GRAPHQL_URL,
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            json={"query": _POST_BY_SLUG_QUERY, "variables": {"slug": slug}},
        )
        resp.raise_for_status()
        body = resp.json()
        node = body.get("data", {}).get("post")
        if node is None:
            return None
        return ProductHuntPost(
            name=node["name"],
            tagline=node["tagline"],
            votes_count=node["votesCount"],
            website=node.get("website"),
            featured_at=node.get("featuredAt"),
        )


__all__ = ["ProductHuntPost", "ProductHuntRetriever"]
