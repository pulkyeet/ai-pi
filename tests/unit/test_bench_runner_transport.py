"""`bench.runner`'s `--cached-only` transport (Phase 14): no Postgres
needed — this just proves the transport itself raises on any request
before it ever reaches a real vendor, the mechanism `bench.yml`'s CI job
relies on to prove a cache seed is complete rather than silently spending
real money on a miss.
"""

from __future__ import annotations

import httpx
import pytest
from bench.runner import CachedOnlyNetworkError, build_runner_http_client


async def test_cached_only_client_raises_on_any_request() -> None:
    client = build_runner_http_client(cached_only=True)
    try:
        with pytest.raises(CachedOnlyNetworkError, match="live network call attempted"):
            await client.get("https://example.com/")
    finally:
        await client.aclose()


async def test_non_cached_only_client_is_a_real_httpx_client() -> None:
    client = build_runner_http_client(cached_only=False)
    try:
        assert isinstance(client, httpx.AsyncClient)
    finally:
        await client.aclose()
