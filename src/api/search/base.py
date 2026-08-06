"""`SearchProvider` protocol and the normalised result shape every provider
must produce (masterplan §7, Phase 04). Kept even with a single v1
implementation (Exa) — it's what makes adding a second provider a new file
rather than a refactor, and what lets tests replay search without touching a
vendor.

`SearchResponse.credits_usd` is the provider's own reported cost for the
call, not a query count — the budget layer (`api.search.router`) cannot
enforce a monthly credit allowance if cost isn't reported back with the
results ("track credits, not calls").
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    url: str
    title: str
    snippet: str = ""
    rank: int
    provider: str
    raw: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    results: list[SearchResult] = Field(default_factory=list)
    credits_usd: float = 0.0
    provider: str
    degraded: bool = False
    degradation_reason: str | None = None


class ProviderError(Exception):
    """A provider call failed after exhausting retries (5xx/timeout/other
    transport failure). Caught by `api.search.router`, never allowed to
    propagate out of `SearchRouter.search()` — see the router's own
    docstring for why degradation is a designed path, not an error path."""

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider


class SearchProvider(Protocol):
    name: str

    async def search(
        self, query: str, *, limit: int = 10, site: str | None = None
    ) -> SearchResponse: ...


__all__ = ["ProviderError", "SearchProvider", "SearchResponse", "SearchResult"]
