"""SERP-snippet reading for G2/Capterra. `api.sources.serp_snippets`
structurally cannot fetch (no `httpx` import at all — see its own module
docstring); this test proves the behavioural half via a fake `SearchProvider`
that never gets asked to do anything but search, plus a direct source-text
check as a belt-and-braces confirmation of the structural guarantee.
"""

from __future__ import annotations

import uuid

import pytest
from _db import insert_run

from api.search.base import SearchResponse, SearchResult
from api.search.router import SearchRouter
from api.sources.serp_snippets import read_aggregator_snippets

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def search(
        self, query: str, *, limit: int = 10, site: str | None = None
    ) -> SearchResponse:
        self.calls.append((query, site))
        return SearchResponse(
            results=[
                SearchResult(
                    url=f"https://{site}/reviews/acme",
                    title="Acme Reviews",
                    snippet="4.5 stars, 120 reviews",
                    rank=0,
                    provider=self.name,
                )
            ],
            credits_usd=0.007,
            provider=self.name,
        )


async def test_reads_g2_snippets_via_search_only(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    provider = FakeProvider()
    router = SearchRouter(pg_pool, provider, run_id=run_id)
    entity = f"Acme{uuid.uuid4().hex[:12]}"

    snippets = await read_aggregator_snippets(router, entity, "g2.com")

    assert provider.calls == [(f"{entity} site:g2.com", "g2.com")]
    assert len(snippets) == 1
    assert snippets[0].domain == "g2.com"
    assert snippets[0].snippet == "4.5 stars, 120 reviews"


async def test_rejects_a_domain_outside_the_no_crawl_set(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    router = SearchRouter(pg_pool, FakeProvider(), run_id=run_id)

    with pytest.raises(ValueError):
        await read_aggregator_snippets(router, "Acme", "not-an-aggregator.com")


def test_module_imports_no_fetch_capable_client() -> None:
    """AST-based, not a substring check: the module's own docstring names
    `httpx`/`api.retrieval.fetch` in prose explaining why it doesn't import
    them, which a naive text search would misfire on."""
    import ast

    import api.sources.serp_snippets as module

    assert module.__file__ is not None
    with open(module.__file__) as f:
        tree = ast.parse(f.read())
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "httpx" not in imported_modules
    assert not any(m.startswith("api.retrieval.fetch") for m in imported_modules)
