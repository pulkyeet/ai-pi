"""Real-cassette proof that `ExaProvider` parses Exa's actual response shape
correctly. `search_exa.yaml` has 50 recorded interactions from Phase 01's
bake-off; `ExaProvider`'s default request shape (`mode="neural"`,
`include_contents=True`) reproduces interaction #37's body byte-for-byte
(verified by inspection — see the Phase 04 plan's decision #5), which is
also the one call with real page `text`, so it's the one used here to prove
`snippet` extraction end to end.
"""

from __future__ import annotations

import httpx
from _vcr import replay_cassette

from api.search.exa import ExaProvider

MATCH_ON = ["method", "scheme", "host", "port", "path", "query", "body"]


async def test_search_parses_real_exa_response() -> None:
    with replay_cassette("search_exa", match_on=MATCH_ON):
        async with httpx.AsyncClient() as client:
            provider = ExaProvider(client, api_key="test-key")
            response = await provider.search("project management tool", limit=10)

    assert response.provider == "exa"
    assert response.credits_usd == 0.007
    assert response.degraded is False
    assert len(response.results) >= 1
    first = response.results[0]
    assert first.title == "OpenProject - Open Source Project Management Software"
    assert first.url == "https://www.openproject.org/"
    assert first.provider == "exa"
    assert first.snippet  # real fetched page text, not empty
    assert first.raw["id"] == "https://www.openproject.org/"
