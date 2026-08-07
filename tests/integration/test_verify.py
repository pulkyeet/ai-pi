"""Artifact verification per scheme (Phase 07, masterplan Rule 2). No
committed cassette exists for these vendor calls — same honestly-flagged
gap as `api.sources.producthunt`'s GraphQL shape — so every scheme is
exercised offline against a scripted `httpx.MockTransport`, matching
`tests/integration/test_github_retriever.py`'s own `repo_metadata` test.

Every scheme value used below is uniquified (`_slug()`/`unique_root()`):
`verification_cache` is a real, TTL'd Postgres table that outlives any
single test run (no per-test rollback, matching this suite's other
Postgres-backed tests), so a literal key like `"acme/widget"` reused across
tests — or across repeated runs of this file — would silently hit a stale
cached result instead of exercising the scripted transport at all.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from _http import PLAIN_HTML, make_client, unique_root
from _http import ScriptedTransport as Transport

from api.models.entity import EntityKey, EntityScheme
from api.resolve.entity_key import derive_web_key
from api.resolve.types import VerificationContext
from api.resolve.verify import verify_entity
from api.retrieval.fetch import HostThrottle
from api.retrieval.robots import RobotsCache
from api.sources.base import RetrieverUnavailableError
from api.sources.github import GitHubRetriever
from api.sources.producthunt import ProductHuntRetriever

pytestmark = pytest.mark.usefixtures("skip_without_postgres")

PARKED_HTML = (
    b"<html><body><h1>this domain is parked</h1><p>Related Searches. "
    b"Buy this domain. Contact the owner for pricing information about "
    b"acquiring this parked domain name for your next project today.</p>"
    b"</body></html>"
)


def _slug() -> str:
    return uuid.uuid4().hex[:12]


def _throttle() -> HostThrottle:
    return HostThrottle(concurrency=2, min_gap_s=0.01)


def _ctx(pg_pool, transport: Transport, **kwargs) -> VerificationContext:
    client = make_client(transport)
    return VerificationContext(
        pool=pg_pool, http=client, throttle=_throttle(), robots=RobotsCache(client), **kwargs
    )


# ---------------------------------------------------------------------------
# web:
# ---------------------------------------------------------------------------


async def test_web_200_is_verified(pg_pool) -> None:
    root = unique_root()
    transport = Transport({"/": [httpx.Response(200, content=PLAIN_HTML)]})
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, derive_web_key(root))

    assert result.verified is True
    assert result.grade == "A"


async def test_web_404_is_discarded(pg_pool) -> None:
    root = unique_root()
    transport = Transport({"/": [httpx.Response(404)]})
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, derive_web_key(root))

    assert result.verified is False
    assert result.reason == "http_404"


async def test_web_parked_page_is_discarded(pg_pool) -> None:
    root = unique_root()
    transport = Transport({"/": [httpx.Response(200, content=PARKED_HTML)]})
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, derive_web_key(root))

    assert result.verified is False
    assert result.reason == "parked_page"


async def test_web_verification_result_is_cached_zero_network_on_second_call(pg_pool) -> None:
    root = unique_root()
    transport = Transport({"/": [httpx.Response(200, content=PLAIN_HTML)]})
    ctx = _ctx(pg_pool, transport)
    key = derive_web_key(root)

    first = await verify_entity(ctx, key)
    calls_after_first = transport.calls["/"]
    second = await verify_entity(ctx, key)

    assert second == first
    assert transport.calls["/"] == calls_after_first


# ---------------------------------------------------------------------------
# gh:
# ---------------------------------------------------------------------------


def _github_repo_response(*, homepage: str | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "full_name": "acme/widget",
            "stargazers_count": 10,
            "open_issues_count": 1,
            "homepage": homepage,
        },
    )


async def test_gh_200_is_verified_and_captures_homepage(pg_pool) -> None:
    repo = _slug()
    transport = Transport(
        {
            f"/repos/acme/{repo}": [_github_repo_response(homepage="https://acme.com")],
            f"/repos/acme/{repo}/contributors": [httpx.Response(200, json=[])],
        }
    )
    client = make_client(transport)
    ctx = VerificationContext(
        pool=pg_pool,
        http=client,
        throttle=_throttle(),
        robots=RobotsCache(client),
        github=GitHubRetriever(client, token="test-token"),
    )

    result = await verify_entity(ctx, EntityKey(EntityScheme.GH, f"acme/{repo}"))

    assert result.verified is True
    assert result.homepage_url == "https://acme.com"


async def test_gh_404_is_discarded(pg_pool) -> None:
    repo = _slug()
    transport = Transport(
        {f"/repos/acme/{repo}": [httpx.Response(404, json={"message": "Not Found"})]}
    )
    client = make_client(transport)
    ctx = VerificationContext(
        pool=pg_pool,
        http=client,
        throttle=_throttle(),
        robots=RobotsCache(client),
        github=GitHubRetriever(client, token="test-token"),
    )

    result = await verify_entity(ctx, EntityKey(EntityScheme.GH, f"acme/{repo}"))

    assert result.verified is False
    assert result.reason == "http_404"


async def test_gh_without_retriever_raises_retriever_unavailable(pg_pool) -> None:
    transport = Transport({})
    ctx = _ctx(pg_pool, transport)

    with pytest.raises(RetrieverUnavailableError):
        await verify_entity(ctx, EntityKey(EntityScheme.GH, f"acme/{_slug()}"))


# ---------------------------------------------------------------------------
# npm: / pypi:
# ---------------------------------------------------------------------------


async def test_npm_200_captures_repository_url(pg_pool) -> None:
    pkg = _slug()
    transport = Transport(
        {
            f"/{pkg}": [
                httpx.Response(
                    200,
                    json={
                        "name": pkg,
                        "repository": {"url": "git+https://github.com/acme/widget.git"},
                    },
                )
            ]
        }
    )
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, EntityKey(EntityScheme.NPM, pkg))

    assert result.verified is True
    assert result.repository_url == "git+https://github.com/acme/widget.git"


async def test_npm_404_is_discarded(pg_pool) -> None:
    pkg = _slug()
    transport = Transport({f"/{pkg}": [httpx.Response(404)]})
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, EntityKey(EntityScheme.NPM, pkg))

    assert result.verified is False


async def test_pypi_200_captures_repository_url_from_project_urls(pg_pool) -> None:
    pkg = _slug()
    transport = Transport(
        {
            f"/pypi/{pkg}/json": [
                httpx.Response(
                    200,
                    json={
                        "info": {
                            "project_urls": {
                                "Homepage": "https://acme.com",
                                "Source": "https://github.com/acme/widget",
                            }
                        }
                    },
                )
            ]
        }
    )
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, EntityKey(EntityScheme.PYPI, pkg))

    assert result.verified is True
    assert result.repository_url == "https://github.com/acme/widget"


async def test_pypi_404_is_discarded(pg_pool) -> None:
    pkg = _slug()
    transport = Transport({f"/pypi/{pkg}/json": [httpx.Response(404)]})
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, EntityKey(EntityScheme.PYPI, pkg))

    assert result.verified is False


# ---------------------------------------------------------------------------
# chrome: / ios: / hf:
# ---------------------------------------------------------------------------


async def test_chrome_200_is_verified(pg_pool) -> None:
    ext_id = _slug()
    transport = Transport({f"/detail/{ext_id}": [httpx.Response(200, content=PLAIN_HTML)]})
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, EntityKey(EntityScheme.CHROME, ext_id))

    assert result.verified is True


async def test_chrome_404_is_discarded(pg_pool) -> None:
    ext_id = _slug()
    transport = Transport({f"/detail/{ext_id}": [httpx.Response(404)]})
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, EntityKey(EntityScheme.CHROME, ext_id))

    assert result.verified is False


async def test_ios_found_is_verified(pg_pool) -> None:
    transport = Transport(
        {"/lookup": [httpx.Response(200, json={"resultCount": 1, "results": [{}]})]}
    )
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, EntityKey(EntityScheme.IOS, _slug()))

    assert result.verified is True


async def test_ios_not_found_is_discarded(pg_pool) -> None:
    transport = Transport(
        {"/lookup": [httpx.Response(200, json={"resultCount": 0, "results": []})]}
    )
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, EntityKey(EntityScheme.IOS, _slug()))

    assert result.verified is False


async def test_hf_model_found_is_verified(pg_pool) -> None:
    repo_id = f"acme/{_slug()}"
    transport = Transport({f"/api/models/{repo_id}": [httpx.Response(200, json={"id": repo_id})]})
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, EntityKey(EntityScheme.HF, repo_id))

    assert result.verified is True


async def test_hf_falls_back_to_spaces_when_model_404s(pg_pool) -> None:
    repo_id = f"acme/{_slug()}"
    transport = Transport(
        {
            f"/api/models/{repo_id}": [httpx.Response(404)],
            f"/api/spaces/{repo_id}": [httpx.Response(200, json={"id": repo_id})],
        }
    )
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, EntityKey(EntityScheme.HF, repo_id))

    assert result.verified is True


async def test_hf_neither_model_nor_space_is_discarded(pg_pool) -> None:
    repo_id = f"acme/{_slug()}"
    transport = Transport(
        {
            f"/api/models/{repo_id}": [httpx.Response(404)],
            f"/api/spaces/{repo_id}": [httpx.Response(404)],
        }
    )
    ctx = _ctx(pg_pool, transport)

    result = await verify_entity(ctx, EntityKey(EntityScheme.HF, repo_id))

    assert result.verified is False


# ---------------------------------------------------------------------------
# ph:
# ---------------------------------------------------------------------------


async def test_ph_post_found_is_verified(pg_pool) -> None:
    transport = Transport(
        {
            "/v2/api/graphql": [
                httpx.Response(
                    200,
                    json={
                        "data": {
                            "post": {"name": "Widget", "tagline": "A widget", "votesCount": 10}
                        }
                    },
                )
            ]
        }
    )
    client = make_client(transport)
    ctx = VerificationContext(
        pool=pg_pool,
        http=client,
        throttle=_throttle(),
        robots=RobotsCache(client),
        producthunt=ProductHuntRetriever(client, token="test-token"),
    )

    result = await verify_entity(ctx, EntityKey(EntityScheme.PH, _slug()))

    assert result.verified is True
    assert result.grade == "B"


async def test_ph_post_not_found_is_discarded(pg_pool) -> None:
    transport = Transport({"/v2/api/graphql": [httpx.Response(200, json={"data": {"post": None}})]})
    client = make_client(transport)
    ctx = VerificationContext(
        pool=pg_pool,
        http=client,
        throttle=_throttle(),
        robots=RobotsCache(client),
        producthunt=ProductHuntRetriever(client, token="test-token"),
    )

    result = await verify_entity(ctx, EntityKey(EntityScheme.PH, _slug()))

    assert result.verified is False


async def test_ph_without_retriever_raises_retriever_unavailable(pg_pool) -> None:
    transport = Transport({})
    ctx = _ctx(pg_pool, transport)

    with pytest.raises(RetrieverUnavailableError):
        await verify_entity(ctx, EntityKey(EntityScheme.PH, _slug()))
