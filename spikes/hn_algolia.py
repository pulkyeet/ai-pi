"""Phase 01 spike: HN Algolia search API. No auth required.

Verifies: reachability, observed rate limit (from headers, if any), and that
both relevance and date-sorted search work as the masterplan's community
mining path expects.

Run: uv run python spikes/hn_algolia.py
"""

from __future__ import annotations

import json

import httpx
from _common import hr, timed_runs, vcr_cassette

BASE = "https://hn.algolia.com/api/v1"
QUERY = "linear alternative"


def search_relevance(client: httpx.Client) -> httpx.Response:
    return client.get(f"{BASE}/search", params={"query": QUERY})


def search_by_date(client: httpx.Client) -> httpx.Response:
    return client.get(f"{BASE}/search_by_date", params={"query": QUERY, "tags": "story"})


def main() -> None:
    with vcr_cassette("hn_algolia"), httpx.Client(timeout=10) as client:
        hr("HN Algolia — relevance search")
        responses, stats = timed_runs(lambda: search_relevance(client), n=3)
        r = responses[-1]
        body = r.json()
        print(f"status={r.status_code} nb_hits={body.get('nbHits')} hits_returned={len(body.get('hits', []))}")
        print(f"latency p50={stats['p50_ms']:.0f}ms p95={stats['p95_ms']:.0f}ms (n={int(stats['n'])})")
        rate_headers = {k: v for k, v in r.headers.items() if "rate" in k.lower() or "limit" in k.lower()}
        print(f"rate-limit headers observed: {rate_headers or 'none'}")

        hr("HN Algolia — search_by_date")
        r2 = search_by_date(client)
        body2 = r2.json()
        print(f"status={r2.status_code} nb_hits={body2.get('nbHits')}")
        if body2.get("hits"):
            first = body2["hits"][0]
            print(f"most recent hit created_at={first.get('created_at')} title={first.get('title')!r}")

        hr("Sample hit fields (for extraction schema design)")
        if body.get("hits"):
            print(json.dumps({k: body["hits"][0].get(k) for k in ("title", "url", "points", "num_comments", "created_at")}, indent=2))


if __name__ == "__main__":
    main()
