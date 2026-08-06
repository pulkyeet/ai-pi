"""npm/PyPI download-count retriever against the real Phase 01 cassette
(`tests/fixtures/cassettes/packages.yaml`, packages `react` / `requests`)."""

from __future__ import annotations

import httpx
from _vcr import replay_cassette

from api.sources.packages import PackagesRetriever


async def test_npm_downloads_parses_real_response() -> None:
    with replay_cassette("packages"):
        async with httpx.AsyncClient() as client:
            retriever = PackagesRetriever(client)
            result = await retriever.npm_downloads("react")

    assert result is not None
    assert result.package == "react"
    assert result.downloads == 163919885


async def test_pypi_downloads_parses_real_response() -> None:
    with replay_cassette("packages"):
        async with httpx.AsyncClient() as client:
            retriever = PackagesRetriever(client)
            result = await retriever.pypi_downloads("requests")

    assert result is not None
    assert result.package == "requests"
    assert result.downloads == 423409518
