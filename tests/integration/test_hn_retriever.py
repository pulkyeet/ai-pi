"""HN Algolia retriever against the real Phase 01 cassette
(`tests/fixtures/cassettes/hn_algolia.yaml`, query `"linear alternative"`)."""

from __future__ import annotations

import uuid

import httpx
from _http import ScriptedTransport, make_client
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


async def test_search_cache_survives_a_new_retriever_instance(pg_pool) -> None:
    query = f"persistent hn cache {uuid.uuid4().hex}"
    transport = ScriptedTransport(
        {
            "/api/v1/search": [
                httpx.Response(
                    200,
                    json={"hits": [{"title": "Cached story", "points": 3}]},
                )
            ]
        }
    )
    client = make_client(transport)

    first = await HNRetriever(client, pool=pg_pool).search(query)
    second = await HNRetriever(client, pool=pg_pool).search(query)

    assert first == second
    assert transport.calls["/api/v1/search"] == 1
    await client.aclose()
