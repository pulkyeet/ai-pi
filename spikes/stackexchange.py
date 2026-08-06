"""Phase 01 spike: Stack Exchange API. Free anonymous quota, no key required.

Verifies: anonymous quota exists and its remaining count is readable, and that
a filter can pull question bodies in the same call (avoids a second round-trip
per question during extraction).

Note: the phase doc says "Free quota (headers report remaining)" — verify
that assumption; Stack Exchange in fact reports quota_max/quota_remaining as
top-level JSON fields on every response, not as HTTP headers. Record whichever
is actually true.

Run: uv run python spikes/stackexchange.py
"""

from __future__ import annotations

import httpx
from _common import hr, timed_runs, vcr_cassette

BASE = "https://api.stackexchange.com/2.3"


def search_questions(client: httpx.Client) -> httpx.Response:
    return client.get(
        f"{BASE}/search/advanced",
        params={
            "order": "desc",
            "sort": "relevance",
            "q": "project management tool alternative",
            "site": "stackoverflow",
            "filter": "withbody",
            "pagesize": 5,
        },
    )


def main() -> None:
    with vcr_cassette("stackexchange"), httpx.Client(timeout=10) as client:
        hr("Stack Exchange — advanced search with withbody filter")
        responses, stats = timed_runs(lambda: search_questions(client), n=3)
        r = responses[-1]
        body = r.json()
        print(f"status={r.status_code} items={len(body.get('items', []))}")
        print(f"latency p50={stats['p50_ms']:.0f}ms p95={stats['p95_ms']:.0f}ms")

        hr("Quota — checking body fields vs headers")
        print(f"body quota_max={body.get('quota_max')} quota_remaining={body.get('quota_remaining')}")
        rate_headers = {k: v for k, v in r.headers.items() if "quota" in k.lower() or "rate" in k.lower()}
        print(f"headers matching quota/rate: {rate_headers or 'none — quota is body-only, not headers'}")

        if body.get("items"):
            item = body["items"][0]
            has_body = "body" in item and bool(item["body"])
            print(f"first item has question body via filter: {has_body} (title={item.get('title')!r})")


if __name__ == "__main__":
    main()
