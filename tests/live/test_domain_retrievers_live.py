"""Nightly live-drift checks for Phase 04's retrievers themselves — unlike
`tests/live/test_vendors.py` (raw HTTP, Phase 01), these exercise
`api.search.exa`/`api.sources.*` directly, proving the *parsing* still
matches the real vendor shape, not just that the vendor is reachable.
Non-blocking: marked `live`, excluded from the default run
(`pyproject.toml`'s `-m 'not live'`), and a failure here means a vendor
changed shape, not that our code regressed.
"""

from __future__ import annotations

import os

import httpx
import pytest

from api.search.exa import ExaProvider
from api.sources.github import GitHubRetriever
from api.sources.hn import HNRetriever
from api.sources.packages import PackagesRetriever
from api.sources.stackexchange import StackExchangeRetriever
from api.sources.wayback import WaybackRetriever

pytestmark = pytest.mark.live


async def test_hn_retriever_live() -> None:
    async with httpx.AsyncClient() as client:
        hits = await HNRetriever(client).search("linear alternative")
    assert hits
    assert hits[0].title


async def test_wayback_retriever_live() -> None:
    async with httpx.AsyncClient() as client:
        snapshots = await WaybackRetriever(client).snapshots("stripe.com")
    assert isinstance(snapshots, list)


async def test_packages_retriever_live() -> None:
    async with httpx.AsyncClient() as client:
        retriever = PackagesRetriever(client)
        npm = await retriever.npm_downloads("react")
        pypi = await retriever.pypi_downloads("requests")
    assert npm is not None and npm.downloads > 0
    assert pypi is not None and pypi.downloads > 0


async def test_stackexchange_retriever_live() -> None:
    async with httpx.AsyncClient() as client:
        questions = await StackExchangeRetriever(client).search("project management tool", limit=1)
    assert isinstance(questions, list)


@pytest.mark.skipif(not os.environ.get("GITHUB_TOKEN"), reason="GITHUB_TOKEN not set")
async def test_github_retriever_live() -> None:
    async with httpx.AsyncClient() as client:
        retriever = GitHubRetriever(client, token=os.environ["GITHUB_TOKEN"])
        repo = await retriever.repo_metadata("microsoft", "vscode")
    assert repo.stargazers_count > 0


@pytest.mark.skipif(not os.environ.get("EXA_API_KEY"), reason="EXA_API_KEY not set")
async def test_exa_provider_live() -> None:
    async with httpx.AsyncClient() as client:
        provider = ExaProvider(client, os.environ["EXA_API_KEY"])
        response = await provider.search("project management tool", limit=3)
    assert response.results
    assert response.credits_usd > 0
