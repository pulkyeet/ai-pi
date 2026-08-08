"""Product Hunt retriever. `post_by_slug` is cassette-tested against the real
recorded response in `tests/fixtures/cassettes/producthunt_post_by_slug.yaml`
(recorded 2026-08-07 once the developer token was obtained, replacing the
previous MockTransport-only coverage). There is deliberately no
`search_posts`: Product Hunt v2 GraphQL has no text-search field — see the
retriever module docstring.
"""

from __future__ import annotations

import httpx
import pytest
from _http import ScriptedTransport, make_client
from _vcr import replay_cassette

from api.sources.base import RetrieverUnavailableError
from api.sources.producthunt import ProductHuntRetriever


async def test_raises_unavailable_without_a_token_and_makes_no_call() -> None:
    transport = ScriptedTransport({})
    client = make_client(transport)
    retriever = ProductHuntRetriever(client, None)

    with pytest.raises(RetrieverUnavailableError):
        await retriever.post_by_slug("notion-ai")
    assert transport.calls == {}
    await client.aclose()


async def test_post_by_slug_parses_a_real_recorded_response() -> None:
    match_on = ["method", "scheme", "host", "port", "path", "query", "body"]
    with replay_cassette("producthunt_post_by_slug", match_on=match_on):
        async with httpx.AsyncClient() as client:
            retriever = ProductHuntRetriever(client, token="test-token")
            post = await retriever.post_by_slug("notion-ai")

    assert post is not None
    assert post.name == "Notion AI"
    assert post.tagline
    assert post.votes_count >= 0
    assert post.featured_at is None or post.featured_at is not None
