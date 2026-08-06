"""Phase 01 spike: npm + PyPI download-count endpoints. No auth required.

Verifies exact download numbers are retrievable (masterplan §5 grades this
signal "A" — highest confidence, so it needs to actually return real numbers,
not just 200 OK).

Run: uv run python spikes/packages.py
"""

from __future__ import annotations

import httpx
from _common import hr, timed_runs, vcr_cassette

NPM_PACKAGE = "react"
PYPI_PACKAGE = "requests"


def npm_downloads(client: httpx.Client) -> httpx.Response:
    return client.get(f"https://api.npmjs.org/downloads/point/last-week/{NPM_PACKAGE}")


def pypi_downloads(client: httpx.Client) -> httpx.Response:
    return client.get(f"https://pypistats.org/api/packages/{PYPI_PACKAGE}/recent")


def main() -> None:
    with vcr_cassette("packages"), httpx.Client(timeout=10) as client:
        hr(f"npm downloads/point/last-week for {NPM_PACKAGE!r}")
        responses, stats = timed_runs(lambda: npm_downloads(client), n=3)
        r = responses[-1]
        print(f"status={r.status_code} body={r.json() if r.status_code == 200 else r.text[:200]}")
        print(f"latency p50={stats['p50_ms']:.0f}ms p95={stats['p95_ms']:.0f}ms")

        hr(f"PyPI (via pypistats.org) recent downloads for {PYPI_PACKAGE!r}")
        r2 = pypi_downloads(client)
        print(f"status={r2.status_code} body={r2.json() if r2.status_code == 200 else r2.text[:200]}")


if __name__ == "__main__":
    main()
