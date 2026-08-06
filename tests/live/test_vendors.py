"""Nightly live-drift checks: hit each real vendor and assert the response
shape still matches what the committed cassette recorded. Non-blocking —
marked `live` and excluded from the default test run (see pyproject.toml).

Failures here mean a vendor changed its API shape, not that our code is
broken; see docs/execution_phases/phase-01-dependency-validation-spike.md.
"""

from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.live


def test_hn_algolia_search_shape() -> None:
    r = httpx.get("https://hn.algolia.com/api/v1/search", params={"query": "test"}, timeout=10)
    r.raise_for_status()
    body = r.json()
    assert "hits" in body and "nbHits" in body


def test_wayback_cdx_shape() -> None:
    r = httpx.get(
        "http://web.archive.org/cdx/search/cdx",
        params={"url": "stripe.com", "matchType": "domain", "output": "json", "limit": 5},
        timeout=30,
    )
    r.raise_for_status()
    rows = r.json()
    assert isinstance(rows, list) and rows[0] == [
        "urlkey",
        "timestamp",
        "original",
        "mimetype",
        "statuscode",
        "digest",
        "length",
    ]


def test_npm_downloads_shape() -> None:
    r = httpx.get("https://api.npmjs.org/downloads/point/last-week/react", timeout=10)
    r.raise_for_status()
    body = r.json()
    assert {"downloads", "start", "end", "package"} <= body.keys()


def test_stackexchange_quota_is_body_not_headers() -> None:
    params = {
        "order": "desc",
        "sort": "relevance",
        "q": "test",
        "site": "stackoverflow",
        "pagesize": 1,
    }
    url = "https://api.stackexchange.com/2.3/search/advanced"
    r = httpx.get(url, params=params, timeout=10)
    r.raise_for_status()
    body = r.json()
    assert "quota_max" in body and "quota_remaining" in body


@pytest.mark.skipif(not os.environ.get("GITHUB_TOKEN"), reason="GITHUB_TOKEN not set")
def test_github_rest_shape() -> None:
    token = os.environ["GITHUB_TOKEN"]
    r = httpx.get(
        "https://api.github.com/rate_limit",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    r.raise_for_status()
    body = r.json()
    assert "resources" in body and "core" in body["resources"]


@pytest.mark.skipif(not os.environ.get("EXA_API_KEY"), reason="EXA_API_KEY not set")
def test_exa_search_shape() -> None:
    key = os.environ["EXA_API_KEY"]
    r = httpx.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": key},
        json={"query": "project management software", "numResults": 3},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    assert "results" in body


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set")
def test_openrouter_models_shape() -> None:
    key = os.environ["OPENROUTER_API_KEY"]
    r = httpx.get(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=10,
    )
    r.raise_for_status()
    body = r.json()
    assert "data" in body
