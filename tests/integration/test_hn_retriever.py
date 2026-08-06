"""HN Algolia retriever against the real Phase 01 cassette
(`tests/fixtures/cassettes/hn_algolia.yaml`, query `"linear alternative"`)."""

from __future__ import annotations

import httpx
from _vcr import replay_cassette

from api.sources.hn import HNRetriever


async def test_search_relevance_parses_real_response() -> None:
    with replay_cassette("hn_algolia"):
        async with httpx.AsyncClient() as client:
            retriever = HNRetriever(client)
            hits = await retriever.search("linear alternative")

    assert hits
    assert hits[0].title


async def test_search_by_date_parses_real_response() -> None:
    with replay_cassette("hn_algolia"):
        async with httpx.AsyncClient() as client:
            retriever = HNRetriever(client)
            hits = await retriever.search("linear alternative", by_date=True)

    assert isinstance(hits, list)
