"""SERP-snippet reading for anti-bot aggregators (masterplan §5): G2 and
Capterra are **never crawled**, only read through search-result snippets.

Structurally, not just conventionally, safe: this module imports only
`api.search` types — no `httpx`, no `api.retrieval.fetch` anywhere in it —
so there is no code path here that could issue a direct fetch to G2 or
Capterra even by mistake. It reuses `api.retrieval.robots.NO_CRAWL_DOMAINS`
as the domain allowlist rather than redeclaring it, so the no-crawl set has
exactly one source of truth.
"""

from __future__ import annotations

from pydantic import BaseModel

from api.retrieval.robots import NO_CRAWL_DOMAINS
from api.search.budget import RetrievalBudget
from api.search.router import SearchRouter

GRADE = "C"  # masterplan §5: SERP-normalised text, deliberately weak evidence


class AggregatorSnippet(BaseModel):
    domain: str
    url: str
    title: str
    snippet: str
    rank: int


async def read_aggregator_snippets(
    router: SearchRouter,
    entity_name: str,
    domain: str,
    *,
    budget: RetrievalBudget | None = None,
    limit: int = 5,
) -> list[AggregatorSnippet]:
    if domain not in NO_CRAWL_DOMAINS:
        raise ValueError(f"{domain!r} is not a registered no-crawl aggregator domain")
    response = await router.search(
        f"{entity_name} site:{domain}", limit=limit, site=domain, budget=budget
    )
    return [
        AggregatorSnippet(domain=domain, url=r.url, title=r.title, snippet=r.snippet, rank=r.rank)
        for r in response.results
    ]


__all__ = ["AggregatorSnippet", "read_aggregator_snippets"]
