"""Wayback CDX retriever against the real Phase 01 cassette
(`tests/fixtures/cassettes/wayback_cdx.yaml`, domain `stripe.com`)."""

from __future__ import annotations

import httpx
from _vcr import replay_cassette

from api.sources.wayback import WaybackRetriever


async def test_snapshots_parses_real_cdx_response() -> None:
    with replay_cassette("wayback_cdx"):
        async with httpx.AsyncClient() as client:
            retriever = WaybackRetriever(client)
            snapshots = await retriever.snapshots("stripe.com")

    assert snapshots
    assert snapshots[0].timestamp
    assert snapshots[0].original
