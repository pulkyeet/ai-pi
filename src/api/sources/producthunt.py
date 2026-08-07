"""Product Hunt retriever (masterplan §5) — launch date, tagline, upvotes.
Grade B. Free GraphQL token, but Phase 01 flagged it **PENDING**: manual
app registration was never completed (`docs/external_apis.md`), so there is
no cassette for this vendor and the GraphQL shape below is unverified
against a real response. Implemented anyway per the phase doc's deliverable
list; raises `RetrieverUnavailableError` immediately (no network call) when
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

_SEARCH_QUERY = """
query($query: String!, $first: Int!) {
  posts(first: $first, order: VOTES) {
    edges {
      node {
        name
        tagline
        votesCount
        website
        featuredAt
      }
    }
  }
}
"""

# Slug lookup, distinct from `_SEARCH_QUERY`'s relevance search: Phase 07's
# `ph:` artifact verification (masterplan §4.5) needs "does this exact post
# exist", not "what posts match this text".
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

    async def search_posts(self, query: str, *, limit: int = 5) -> list[ProductHuntPost]:
        if not self._token:
            raise RetrieverUnavailableError(
                self.name, "no developer token configured (registration pending)"
            )
        await self._limiter.acquire()
        resp = await self._client.post(
            GRAPHQL_URL,
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            json={"query": _SEARCH_QUERY, "variables": {"query": query, "first": limit}},
        )
        resp.raise_for_status()
        body = resp.json()
        edges = body.get("data", {}).get("posts", {}).get("edges", [])
        return [
            ProductHuntPost(
                name=e["node"]["name"],
                tagline=e["node"]["tagline"],
                votes_count=e["node"]["votesCount"],
                website=e["node"].get("website"),
                featured_at=e["node"].get("featuredAt"),
            )
            for e in edges
        ]

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
