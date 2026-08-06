"""Wayback Machine CDX retriever (masterplan §5) — historical snapshots for
"when did X ship" / pricing-history questions. Grade B. No auth, no observed
rate limit, but genuinely slow (~3s p95 measured in Phase 01,
`docs/external_apis.md`) — a generous timeout and a low local rate both
matter more here than anywhere else in this package; call it sparingly and
keep it off any latency-sensitive path (planner-gated, per the phase doc).
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from api.sources.ratelimit import TokenBucket

CDX_URL = "http://web.archive.org/cdx/search/cdx"
TIMEOUT_S = 30.0
RATE_PER_S = 1.0


class WaybackSnapshot(BaseModel):
    timestamp: str
    original: str
    statuscode: str | None = None


class WaybackRetriever:
    name = "wayback_cdx"
    grade = "B"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._limiter = TokenBucket(RATE_PER_S, capacity=1)

    async def snapshots(self, domain: str, *, limit: int = 50) -> list[WaybackSnapshot]:
        await self._limiter.acquire()
        resp = await self._client.get(
            CDX_URL,
            params={
                "url": domain,
                "matchType": "domain",
                "output": "json",
                "limit": limit,
                "filter": "statuscode:200",
                "collapse": "urlkey",
            },
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        rows = resp.json()
        if len(rows) < 2:
            return []
        header, data_rows = rows[0], rows[1:]
        idx = {name: i for i, name in enumerate(header)}
        return [
            WaybackSnapshot(
                timestamp=row[idx["timestamp"]],
                original=row[idx["original"]],
                statuscode=row[idx.get("statuscode", -1)] if "statuscode" in idx else None,
            )
            for row in data_rows
        ]


__all__ = ["WaybackRetriever", "WaybackSnapshot"]
