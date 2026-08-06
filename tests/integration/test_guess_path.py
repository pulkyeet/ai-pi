"""`guess_path` orchestration: stops at first success, caps attempts, caches
positive and negative outcomes, and logs every attempt for the hit-rate
measurement in `docs/external_apis.md`.
"""

from __future__ import annotations

import httpx
import pytest
from _http import PLAIN_HTML, PRICING_HTML, ScriptedTransport, make_client, unique_root

from api.retrieval import cache as source_cache
from api.retrieval.fetch import HostThrottle
from api.retrieval.pathguess import MAX_ATTEMPTS_PER_DOMAIN, PathGuessResult, guess_path
from api.retrieval.robots import RobotsCache

pytestmark = pytest.mark.usefixtures("skip_without_postgres")

CANDIDATE_PATHS = (
    "/pricing",
    "/plans",
    "/pricing-plans",
    "/price",
    "/pricing/",
    "/plans-and-pricing",
    "/subscribe",
)


def _throttle() -> HostThrottle:
    return HostThrottle(concurrency=2, min_gap_s=0.01)


async def _guess(
    pg_pool, client: httpx.AsyncClient, root: str, kind: str = "pricing"
) -> PathGuessResult:
    robots = RobotsCache(client)
    return await guess_path(pg_pool, client, _throttle(), robots, root, kind, retrieval_reason="t")


async def test_stops_at_first_success(pg_pool) -> None:
    root = unique_root()
    transport = ScriptedTransport(
        {
            "/pricing": [httpx.Response(404)],
            "/plans": [httpx.Response(200, content=PRICING_HTML)],
            "/pricing-plans": [httpx.Response(200, content=PRICING_HTML)],
        }
    )
    client = make_client(transport)

    result = await _guess(pg_pool, client, root)

    assert result.found_path == "/plans"
    assert result.from_cache is False
    assert transport.calls["/pricing"] == 1
    assert transport.calls["/plans"] == 1
    assert transport.calls["/pricing-plans"] == 0  # never reached — stopped at first success

    rate = await source_cache.path_guess_hit_rate(pg_pool, path_kind="pricing")
    assert rate is not None


async def test_second_call_is_served_from_cache(pg_pool) -> None:
    root = unique_root()
    transport = ScriptedTransport({"/pricing": [httpx.Response(200, content=PRICING_HTML)]})
    client = make_client(transport)

    first = await _guess(pg_pool, client, root)
    assert first.found_path == "/pricing"
    assert transport.calls["/pricing"] == 1

    second = await _guess(pg_pool, client, root)
    assert second.found_path == "/pricing"
    assert second.from_cache is True
    assert transport.calls["/pricing"] == 1  # no new network call


async def test_all_candidates_miss_caches_negative_result(pg_pool) -> None:
    root = unique_root()
    # Non-price-shaped 200 counts as a miss for the "pricing" kind.
    script = {p: [httpx.Response(200, content=PLAIN_HTML)] for p in CANDIDATE_PATHS[:4]}
    transport = ScriptedTransport(script)
    client = make_client(transport)

    result = await _guess(pg_pool, client, root)

    assert result.found_path is None
    cached = await source_cache.get_path_guess(pg_pool, root, "pricing")
    assert cached is not None
    assert cached.found_path is None


async def test_attempt_cap_is_respected(pg_pool) -> None:
    root = unique_root()
    script = {p: [httpx.Response(404)] for p in CANDIDATE_PATHS}
    transport = ScriptedTransport(script)
    client = make_client(transport)

    await _guess(pg_pool, client, root)

    attempted = sum(1 for p in CANDIDATE_PATHS if transport.calls[p] > 0)
    assert attempted == MAX_ATTEMPTS_PER_DOMAIN
