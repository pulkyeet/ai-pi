"""Product Hunt retriever. No Phase 01 cassette exists for this vendor — the
developer token was never obtained (`docs/external_apis.md`: PENDING), so
this is `httpx.MockTransport`-tested against a synthetic GraphQL response
rather than cassette-tested, called out explicitly rather than silently
implied otherwise.
"""

from __future__ import annotations

import httpx
import pytest
from _http import ScriptedTransport, make_client

from api.sources.base import RetrieverUnavailableError
from api.sources.producthunt import ProductHuntRetriever


async def test_raises_unavailable_without_a_token_and_makes_no_call() -> None:
    transport = ScriptedTransport({})
    client = make_client(transport)
    retriever = ProductHuntRetriever(client, None)

    with pytest.raises(RetrieverUnavailableError):
        await retriever.search_posts("widget")
    assert transport.calls == {}
    await client.aclose()


async def test_search_posts_parses_a_synthetic_graphql_response() -> None:
    body = {
        "data": {
            "posts": {
                "edges": [
                    {
                        "node": {
                            "name": "Widget",
                            "tagline": "The widget everyone needs",
                            "votesCount": 42,
                            "website": "https://widget.example",
                            "featuredAt": "2026-01-01T00:00:00Z",
                        }
                    }
                ]
            }
        }
    }
    transport = ScriptedTransport({"/v2/api/graphql": [httpx.Response(200, json=body)]})
    client = make_client(transport)
    retriever = ProductHuntRetriever(client, "test-token")

    posts = await retriever.search_posts("widget")

    assert len(posts) == 1
    assert posts[0].name == "Widget"
    assert posts[0].votes_count == 42
    await client.aclose()
