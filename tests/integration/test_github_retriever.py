"""GitHub retriever. `issues_by_reactions` and the `star_velocity_90d` 403
replay real Phase 01 traffic from `tests/fixtures/cassettes/github_api.yaml`
(repo `microsoft/vscode`, exactly the spike's literal inputs). No cassette
covers plain `/repos/{owner}/{repo}` (Phase 01 never called it), so
`repo_metadata` is exercised against a scripted `httpx.MockTransport`
instead — called out here explicitly rather than silently claimed as
cassette-tested.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from _http import ScriptedTransport, make_client
from _vcr import replay_cassette

from api.sources.base import RetrieverUnavailableError
from api.sources.github import GitHubRetriever


async def test_issues_by_reactions_parses_real_response() -> None:
    with replay_cassette("github_api"):
        async with httpx.AsyncClient() as client:
            retriever = GitHubRetriever(client, token="test-token")
            issues = await retriever.issues_by_reactions(
                "microsoft", "vscode", label="feature-request", limit=5
            )

    assert isinstance(issues, list)
    for issue in issues:
        assert issue.repo == "microsoft/vscode"
        assert issue.reactions_total >= 0


async def test_star_velocity_degrades_on_the_real_recorded_403() -> None:
    with replay_cassette("github_api"):
        async with httpx.AsyncClient() as client:
            retriever = GitHubRetriever(client, token="test-token")
            with pytest.raises(RetrieverUnavailableError):
                # per_page=10 matches the literal recorded in the cassette
                # (Phase 01's spike requested 10, not this method's real
                # default of 100).
                await retriever.star_velocity_90d("microsoft", "vscode", per_page=10)


async def test_repo_metadata_parses_stars_license_and_contributors() -> None:
    transport = ScriptedTransport(
        {
            "/repos/acme/widget": [
                httpx.Response(
                    200,
                    json={
                        "full_name": "acme/widget",
                        "stargazers_count": 1234,
                        "open_issues_count": 12,
                        "license": {"spdx_id": "MIT"},
                        "pushed_at": "2026-08-01T00:00:00Z",
                    },
                )
            ],
            "/repos/acme/widget/contributors": [
                httpx.Response(
                    200,
                    headers={
                        "link": (
                            "<https://api.github.com/repositories/1/contributors"
                            '?per_page=1&anon=true&page=42>; rel="last"'
                        )
                    },
                    json=[{"login": "x"}],
                )
            ],
        }
    )
    client = make_client(transport)
    retriever = GitHubRetriever(client, token="test-token")

    repo = await retriever.repo_metadata("acme", "widget")

    assert repo.full_name == "acme/widget"
    assert repo.stargazers_count == 1234
    assert repo.license == "MIT"
    assert repo.contributors_count == 42
    await client.aclose()


async def test_repo_metadata_without_license_or_link_header_falls_back() -> None:
    transport = ScriptedTransport(
        {
            "/repos/acme/widget": [
                httpx.Response(
                    200,
                    json={
                        "full_name": "acme/widget",
                        "stargazers_count": 5,
                        "open_issues_count": 1,
                        "license": None,
                        "pushed_at": None,
                    },
                )
            ],
            "/repos/acme/widget/contributors": [
                httpx.Response(200, json=[{"login": "a"}, {"login": "b"}])
            ],
        }
    )
    client = make_client(transport)
    retriever = GitHubRetriever(client, token="test-token")

    repo = await retriever.repo_metadata("acme", "widget")

    assert repo.license is None
    assert repo.contributors_count == 2  # falls back to len(body) with no Link header
    await client.aclose()


async def test_general_rate_limit_exhausted_raises_unavailable() -> None:
    transport = ScriptedTransport(
        {"/repos/acme/widget": [httpx.Response(403, headers={"x-ratelimit-remaining": "0"})]}
    )
    client = make_client(transport)
    retriever = GitHubRetriever(client, token="test-token")

    with pytest.raises(RetrieverUnavailableError):
        await retriever.repo_metadata("acme", "widget")
    await client.aclose()


async def test_repository_search_cache_survives_a_new_retriever_instance(pg_pool) -> None:
    query = f"persistent github cache {uuid.uuid4().hex}"
    transport = ScriptedTransport(
        {
            "/search/repositories": [
                httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "full_name": "acme/widget",
                                "html_url": "https://github.com/acme/widget",
                                "description": "A cached widget",
                                "stargazers_count": 12,
                            }
                        ]
                    },
                )
            ]
        }
    )
    client = make_client(transport)

    first = await GitHubRetriever(client, token="test-token", pool=pg_pool).search_repositories(
        query
    )
    second = await GitHubRetriever(client, token="test-token", pool=pg_pool).search_repositories(
        query
    )

    assert first == second
    assert transport.calls["/search/repositories"] == 1
    await client.aclose()


async def test_repo_metadata_cache_survives_a_new_retriever_instance(pg_pool) -> None:
    repo = f"acme/widget-{uuid.uuid4().hex}"
    owner, name = repo.split("/")
    transport = ScriptedTransport(
        {
            f"/repos/{repo}": [
                httpx.Response(
                    200,
                    json={
                        "full_name": repo,
                        "stargazers_count": 42,
                        "open_issues_count": 3,
                        "license": {"spdx_id": "MIT"},
                        "pushed_at": "2026-08-01T00:00:00Z",
                    },
                )
            ],
            f"/repos/{repo}/contributors": [httpx.Response(200, json=[])],
        }
    )
    client = make_client(transport)

    first = await GitHubRetriever(client, token="test-token", pool=pg_pool).repo_metadata(
        owner, name
    )
    second = await GitHubRetriever(client, token="test-token", pool=pg_pool).repo_metadata(
        owner, name
    )

    assert first == second
    assert transport.calls[f"/repos/{repo}"] == 1
    assert transport.calls[f"/repos/{repo}/contributors"] == 1
    await client.aclose()


async def test_star_velocity_unavailable_response_is_cached(pg_pool) -> None:
    repo = f"acme/widget-{uuid.uuid4().hex}"
    owner, name = repo.split("/")
    path = f"/repos/{repo}/stargazers"
    transport = ScriptedTransport({path: [httpx.Response(403)]})
    client = make_client(transport)

    first = GitHubRetriever(client, token="test-token", pool=pg_pool)
    with pytest.raises(RetrieverUnavailableError):
        await first.star_velocity_90d(owner, name)

    second = GitHubRetriever(client, token="test-token", pool=pg_pool)
    with pytest.raises(RetrieverUnavailableError):
        await second.star_velocity_90d(owner, name)

    assert transport.calls[path] == 1
    await client.aclose()
