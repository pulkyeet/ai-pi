"""Stack Exchange retriever against the real Phase 01 cassette
(`tests/fixtures/cassettes/stackexchange.yaml`, query
`"project management tool alternative"`). Confirms the quota is read from
the response body, not headers (`docs/working_knowledge.md` Known Issues)."""

from __future__ import annotations

import httpx
from _vcr import replay_cassette

from api.sources.stackexchange import StackExchangeRetriever


async def test_search_parses_real_response_and_body_quota() -> None:
    with replay_cassette("stackexchange"):
        async with httpx.AsyncClient() as client:
            retriever = StackExchangeRetriever(client)
            questions = await retriever.search("project management tool alternative", limit=5)

    assert questions
    assert questions[0].title
    assert retriever.quota_remaining is not None
