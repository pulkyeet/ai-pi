"""Phase 01 spike: Wayback Machine CDX API. No auth required.

Verifies: snapshot listing for a domain, and measures latency — the phase doc
flags this endpoint as slow, so p50/p95 over 3 runs is the point of this spike,
not just reachability.

Run: uv run python spikes/wayback_cdx.py
"""

from __future__ import annotations

import httpx
from _common import hr, timed_runs, vcr_cassette

CDX_URL = "http://web.archive.org/cdx/search/cdx"
DOMAIN = "stripe.com"


def fetch_snapshots(client: httpx.Client) -> httpx.Response:
    return client.get(
        CDX_URL,
        params={
            "url": DOMAIN,
            "matchType": "domain",
            "output": "json",
            "limit": 50,
            "filter": "statuscode:200",
            "collapse": "urlkey",
        },
    )


def main() -> None:
    with vcr_cassette("wayback_cdx"), httpx.Client(timeout=30, follow_redirects=True) as client:
        hr(f"Wayback CDX — snapshot listing for {DOMAIN}")
        responses, stats = timed_runs(lambda: fetch_snapshots(client), n=3)
        r = responses[-1]
        rows = r.json() if r.status_code == 200 else []
        n_rows = max(0, len(rows) - 1)  # first row is the header
        print(f"status={r.status_code} rows_returned={n_rows}")
        p50, p95, n = stats["p50_ms"], stats["p95_ms"], int(stats["n"])
        print(f"latency p50={p50:.0f}ms p95={p95:.0f}ms (n={n})")
        if len(rows) > 1:
            header, sample = rows[0], rows[1]
            print(f"columns: {header}")
            print(f"sample row: {sample}")


if __name__ == "__main__":
    main()
