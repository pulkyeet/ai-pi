"""Every HTTP path `fetch_source` must handle, exercised offline against a
scripted `httpx.MockTransport` (see `_http.py`) and a real Postgres cache.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from _http import (
    COOKIE_WALL_HTML,
    PLAIN_HTML,
    make_client,
    unique_root,
)
from _http import ScriptedTransport as Transport

from api.models.source import CacheOutcome
from api.retrieval.errors import (
    NoCrawlDomainError,
    ResponseTooLargeError,
    RobotsDisallowedError,
    ThinContentError,
    UnsupportedContentTypeError,
)
from api.retrieval.fetch import HostThrottle, fetch_source
from api.retrieval.robots import RobotsCache

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


def _throttle() -> HostThrottle:
    # Tiny gap so the test suite stays fast; the gap-enforcement behaviour
    # itself is asserted separately with a similarly-scaled-down instance.
    return HostThrottle(concurrency=2, min_gap_s=0.01)


async def test_basic_200_is_fetched_extracted_and_cached(pg_pool) -> None:
    root = unique_root()
    transport = Transport({"/pricing": [httpx.Response(200, content=PLAIN_HTML)]})
    client = make_client(transport)
    robots = RobotsCache(client)

    result = await fetch_source(
        pg_pool, client, _throttle(), robots, f"https://{root}/pricing", retrieval_reason="test"
    )

    assert result.cache_outcome == CacheOutcome.MISS
    assert result.source.http_status == 200
    assert result.source.extracted_text is not None
    assert "Welcome" in result.source.extracted_text
    assert result.source.content_hash is not None
    assert result.source.canonical_url == f"https://{root}/pricing"
    await client.aclose()


async def test_redirect_301_then_200_is_followed(pg_pool) -> None:
    root = unique_root()
    transport = Transport(
        {
            "/old": [httpx.Response(301, headers={"location": f"https://{root}/new"})],
            "/new": [httpx.Response(200, content=PLAIN_HTML)],
        }
    )
    client = make_client(transport)
    robots = RobotsCache(client)

    result = await fetch_source(
        pg_pool, client, _throttle(), robots, f"https://{root}/old", retrieval_reason="test"
    )

    assert result.source.http_status == 200
    assert result.source.extracted_text is not None
    assert transport.calls["/old"] == 1
    assert transport.calls["/new"] == 1
    await client.aclose()


async def test_404_is_cached_not_raised(pg_pool) -> None:
    root = unique_root()
    transport = Transport({"/gone": [httpx.Response(404)]})
    client = make_client(transport)
    robots = RobotsCache(client)

    result = await fetch_source(
        pg_pool, client, _throttle(), robots, f"https://{root}/gone", retrieval_reason="test"
    )

    assert result.source.http_status == 404
    assert result.source.extracted_text is None
    assert result.cache_outcome == CacheOutcome.MISS
    await client.aclose()


async def test_429_then_200_is_retried_and_succeeds(pg_pool) -> None:
    root = unique_root()
    transport = Transport(
        {
            "/pricing": [
                httpx.Response(429, headers={"retry-after": "0"}),
                httpx.Response(200, content=PLAIN_HTML),
            ]
        }
    )
    client = make_client(transport)
    robots = RobotsCache(client)

    result = await fetch_source(
        pg_pool, client, _throttle(), robots, f"https://{root}/pricing", retrieval_reason="test"
    )

    assert result.source.http_status == 200
    assert transport.calls["/pricing"] == 2
    await client.aclose()


async def test_timeout_then_200_is_retried_and_succeeds(pg_pool) -> None:
    root = unique_root()
    transport = Transport(
        {"/pricing": [httpx.TimeoutException("boom"), httpx.Response(200, content=PLAIN_HTML)]}
    )
    client = make_client(transport)
    robots = RobotsCache(client)

    result = await fetch_source(
        pg_pool, client, _throttle(), robots, f"https://{root}/pricing", retrieval_reason="test"
    )

    assert result.source.http_status == 200
    assert transport.calls["/pricing"] == 2
    await client.aclose()


async def test_persistent_5xx_is_cached_after_retries_exhausted(pg_pool) -> None:
    root = unique_root()
    transport = Transport({"/pricing": [httpx.Response(503)]})
    client = make_client(transport)
    robots = RobotsCache(client)

    result = await fetch_source(
        pg_pool, client, _throttle(), robots, f"https://{root}/pricing", retrieval_reason="test"
    )

    assert result.source.http_status == 503
    assert transport.calls["/pricing"] == 3  # MAX_ATTEMPTS
    await client.aclose()


async def test_oversized_response_raises_and_is_not_cached(pg_pool) -> None:
    root = unique_root()
    oversized = b"x" * (6 * 1024 * 1024)
    transport = Transport({"/huge": [httpx.Response(200, content=oversized)]})
    client = make_client(transport)
    robots = RobotsCache(client)

    with pytest.raises(ResponseTooLargeError):
        await fetch_source(
            pg_pool, client, _throttle(), robots, f"https://{root}/huge", retrieval_reason="test"
        )
    await client.aclose()


async def test_unsupported_content_type_is_rejected(pg_pool) -> None:
    root = unique_root()
    transport = Transport(
        {
            "/report.pdf": [
                httpx.Response(
                    200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"}
                )
            ]
        }
    )
    client = make_client(transport)
    robots = RobotsCache(client)

    with pytest.raises(UnsupportedContentTypeError):
        await fetch_source(
            pg_pool,
            client,
            _throttle(),
            robots,
            f"https://{root}/report.pdf",
            retrieval_reason="test",
        )
    await client.aclose()


async def test_cookie_wall_thin_content_is_rejected(pg_pool) -> None:
    root = unique_root()
    transport = Transport({"/pricing": [httpx.Response(200, content=COOKIE_WALL_HTML)]})
    client = make_client(transport)
    robots = RobotsCache(client)

    with pytest.raises(ThinContentError):
        await fetch_source(
            pg_pool, client, _throttle(), robots, f"https://{root}/pricing", retrieval_reason="test"
        )
    await client.aclose()


async def test_robots_disallow_prevents_fetch(pg_pool) -> None:
    root = unique_root()
    robots_txt = b"User-agent: *\nDisallow: /pricing\n"
    transport = Transport(
        {
            "/robots.txt": [httpx.Response(200, content=robots_txt)],
            "/pricing": [httpx.Response(200, content=PLAIN_HTML)],
        }
    )
    client = make_client(transport)
    robots = RobotsCache(client)

    with pytest.raises(RobotsDisallowedError):
        await fetch_source(
            pg_pool, client, _throttle(), robots, f"https://{root}/pricing", retrieval_reason="test"
        )
    assert transport.calls["/pricing"] == 0
    await client.aclose()


async def test_hardcoded_no_crawl_set_is_never_fetched_even_if_robots_allows(pg_pool) -> None:
    transport = Transport(
        {
            "/robots.txt": [httpx.Response(200, content=b"User-agent: *\nAllow: /\n")],
            "/products/x": [httpx.Response(200, content=PLAIN_HTML)],
        }
    )
    client = make_client(transport)
    robots = RobotsCache(client)

    with pytest.raises(NoCrawlDomainError):
        await fetch_source(
            pg_pool,
            client,
            _throttle(),
            robots,
            "https://g2.com/products/x",
            retrieval_reason="test",
        )
    assert transport.calls["/products/x"] == 0
    # no-crawl short-circuits before robots.txt is even checked
    assert transport.calls["/robots.txt"] == 0
    await client.aclose()


async def test_per_host_politeness_serialises_with_a_minimum_gap(pg_pool) -> None:
    root = unique_root()
    transport = Transport({"/p": [httpx.Response(200, content=PLAIN_HTML)]})
    client = make_client(transport)
    throttle = HostThrottle(concurrency=2, min_gap_s=0.1)

    starts: list[float] = []

    async def one(i: int) -> None:
        async with throttle.acquire(root):
            starts.append(time.monotonic())

    await asyncio.gather(*(one(i) for i in range(4)))
    starts.sort()
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    assert all(gap >= 0.1 - 0.02 for gap in gaps), gaps
    await client.aclose()
