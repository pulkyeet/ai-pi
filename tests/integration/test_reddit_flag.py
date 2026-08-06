"""Reddit stays behind `ENABLE_REDDIT` (default off) and behind OAuth
credentials — both conditions degrade to `RetrieverUnavailableError` without
ever touching the network (D5, `docs/execution_phases/README.md`)."""

from __future__ import annotations

import httpx
import pytest
from _http import ScriptedTransport, make_client

from api.sources.base import RetrieverUnavailableError
from api.sources.reddit import RedditRetriever


async def test_disabled_by_default_raises_without_any_network_call() -> None:
    transport = ScriptedTransport({})
    client = make_client(transport)
    retriever = RedditRetriever(client, enabled=False, client_id=None, client_secret=None)

    with pytest.raises(RetrieverUnavailableError):
        await retriever.search("widget")
    assert transport.calls == {}
    await client.aclose()


async def test_enabled_without_credentials_still_degrades() -> None:
    transport = ScriptedTransport({})
    client = make_client(transport)
    retriever = RedditRetriever(client, enabled=True, client_id=None, client_secret=None)

    with pytest.raises(RetrieverUnavailableError):
        await retriever.search("widget")
    assert transport.calls == {}
    await client.aclose()


async def test_enabled_with_credentials_reaches_the_search_endpoint() -> None:
    transport = ScriptedTransport(
        {
            "/api/v1/access_token": [httpx.Response(200, json={"access_token": "tok"})],
            "/search": [
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "children": [
                                {
                                    "data": {
                                        "title": "Widget thread",
                                        "permalink": "/r/x/1",
                                        "score": 42,
                                    }
                                }
                            ]
                        }
                    },
                )
            ],
        }
    )
    client = make_client(transport)
    retriever = RedditRetriever(client, enabled=True, client_id="id", client_secret="secret")

    posts = await retriever.search("widget")

    assert len(posts) == 1
    assert posts[0].title == "Widget thread"
    assert posts[0].score == 42
    await client.aclose()
