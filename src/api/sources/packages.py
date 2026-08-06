"""npm + PyPI download-count retrievers (masterplan §5) — exact adoption
numbers, free, no auth. Grade A, per masterplan §4.6: this is the highest-
confidence signal the system has, so both endpoints need to actually parse
real numbers, not just return 200 OK (verified in Phase 01 against the
committed cassette — see `docs/external_apis.md`).
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from api.sources.ratelimit import TokenBucket

NPM_URL = "https://api.npmjs.org/downloads/point/last-week"
PYPI_URL = "https://pypistats.org/api/packages"
RATE_PER_S = 10.0


class DownloadCount(BaseModel):
    package: str
    downloads: int
    period: str


class PackagesRetriever:
    name = "packages"
    grade = "A"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._limiter = TokenBucket(RATE_PER_S)

    async def npm_downloads(self, package: str) -> DownloadCount | None:
        await self._limiter.acquire()
        resp = await self._client.get(f"{NPM_URL}/{package}")
        if resp.status_code != 200:
            return None
        body = resp.json()
        return DownloadCount(package=package, downloads=body["downloads"], period="last-week")

    async def pypi_downloads(self, package: str) -> DownloadCount | None:
        await self._limiter.acquire()
        resp = await self._client.get(f"{PYPI_URL}/{package}/recent")
        if resp.status_code != 200:
            return None
        body = resp.json()
        return DownloadCount(
            package=package, downloads=body["data"]["last_week"], period="last-week"
        )


__all__ = ["DownloadCount", "PackagesRetriever"]
