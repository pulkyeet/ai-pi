"""Phase 01 spike: Exa search bake-off. Exa is already decided (D1/D2 in
docs/execution_phases/README.md) — this answers whether its thin-query recall
is good enough and how much of the $10/mo allowance one run actually burns.

Fixed 12-query eval set: 4 mainstream, 4 mid-difficulty, 4 thin/niche.
Measures per query: result count, p50/p95 latency over 3 runs, whether known
correct competitors appear in the top 10 (mainstream/mid only — thin queries
have no stable ground truth, so those are recorded qualitatively), and exact
per-query cost in dollars (Exa returns costDollars in every response body —
real dollars, not an opaque credit unit).

Also profiles cost by mode (neural vs keyword, with/without contents text)
and observed behavior under a 10-request burst.

Requires EXA_API_KEY in the environment (see .env).

Run: uv run python spikes/search_exa.py
"""

from __future__ import annotations

import os
import time

import httpx

from _common import hr, timed_runs, vcr_cassette

BASE = "https://api.exa.ai/search"

# (query, category, known-correct domains for a naive recall check; empty for thin queries)
EVAL_SET: list[tuple[str, str, list[str]]] = [
    ("project management tool", "mainstream", ["asana.com", "trello.com", "monday.com", "clickup.com"]),
    ("CRM software", "mainstream", ["salesforce.com", "hubspot.com", "pipedrive.com", "zoho.com"]),
    ("email marketing platform", "mainstream", ["mailchimp.com", "sendgrid.com", "klaviyo.com", "convertkit.com"]),
    ("video conferencing software", "mainstream", ["zoom.us", "meet.google.com", "teams.microsoft.com", "webex.com"]),
    ("asynchronous stand-up tool for remote teams", "mid", ["geekbot.com", "standuply.com", "range.co"]),
    ("customer feedback widget for SaaS", "mid", ["canny.io", "usersnap.com", "hotjar.com"]),
    ("no-code internal tools builder", "mid", ["retool.com", "internal.io", "budibase.com"]),
    ("AI meeting notes app", "mid", ["otter.ai", "fireflies.ai", "fathom.video"]),
    ("MEV monitoring for solo validators", "thin", []),
    ("WhatsApp first CRM for Indian SMBs", "thin", []),
    ("on-call scheduling for game studios", "thin", []),
    ("compliance automation for crypto exchanges in the EU", "thin", []),
]


def headers() -> dict[str, str]:
    return {"x-api-key": os.environ["EXA_API_KEY"], "content-type": "application/json"}


def search(client: httpx.Client, query: str, **kwargs) -> httpx.Response:
    payload = {"query": query, "numResults": 10, **kwargs}
    return client.post(BASE, headers=headers(), json=payload)


def run_eval_set(client: httpx.Client) -> list[dict]:
    rows = []
    for query, category, known_domains in EVAL_SET:
        responses, stats = timed_runs(lambda q=query: search(client, q), n=3)
        r = responses[-1]
        body = r.json()
        results = body.get("results", [])
        urls = [res.get("url", "") for res in results]
        hit = any(any(d in u for u in urls) for d in known_domains) if known_domains else None
        cost = body.get("costDollars", {}).get("total")
        rows.append(
            {
                "query": query,
                "category": category,
                "n_results": len(results),
                "known_domain_in_top10": hit,
                "p50_ms": stats["p50_ms"],
                "p95_ms": stats["p95_ms"],
                "cost_usd": cost,
                "sample_titles": [res.get("title") for res in results[:3]],
            }
        )
    return rows


def mode_cost_comparison(client: httpx.Client) -> list[dict]:
    query = "project management tool"
    modes = [
        ("neural, no contents", {"type": "neural"}),
        ("neural, with text contents", {"type": "neural", "contents": {"text": True}}),
        ("keyword", {"type": "keyword"}),
        ("auto", {"type": "auto"}),
    ]
    rows = []
    for label, kwargs in modes:
        r = search(client, query, **kwargs)
        body = r.json()
        rows.append(
            {
                "mode": label,
                "status": r.status_code,
                "resolvedSearchType": body.get("resolvedSearchType"),
                "cost_usd": body.get("costDollars", {}).get("total"),
                "cost_breakdown": body.get("costDollars"),
            }
        )
    return rows


def burst_test(client: httpx.Client, n: int = 10) -> list[int]:
    statuses = []
    start = time.perf_counter()
    for i in range(n):
        r = search(client, f"burst test query {i}", numResults=1)
        statuses.append(r.status_code)
    elapsed = time.perf_counter() - start
    print(f"burst of {n} requests took {elapsed:.2f}s wall clock; statuses={statuses}")
    return statuses


def main() -> None:
    with vcr_cassette("search_exa"), httpx.Client(timeout=30) as client:
        hr("Exa — 12-query fixed eval set (4 mainstream / 4 mid / 4 thin)")
        rows = run_eval_set(client)
        total_cost = 0.0
        for row in rows:
            hit_str = "-" if row["known_domain_in_top10"] is None else str(row["known_domain_in_top10"])
            print(
                f"[{row['category']:<10}] {row['query']:<48} n={row['n_results']:>2} "
                f"known_hit={hit_str:<5} p50={row['p50_ms']:.0f}ms cost=${row['cost_usd']}"
            )
            for t in row["sample_titles"]:
                print(f"      - {t}")
            total_cost += row["cost_usd"] or 0.0

        mainstream = [r for r in rows if r["category"] == "mainstream"]
        mid = [r for r in rows if r["category"] == "mid"]
        thin = [r for r in rows if r["category"] == "thin"]
        print(f"\nmainstream recall: {sum(1 for r in mainstream if r['known_domain_in_top10'])}/{len(mainstream)}")
        print(f"mid recall:        {sum(1 for r in mid if r['known_domain_in_top10'])}/{len(mid)}")
        print(f"thin: all returned n_results>0: {all(r['n_results'] > 0 for r in thin)} (see titles above for usability)")
        print(f"\ntotal cost for {len(rows)} queries (numResults=10 each): ${total_cost:.4f}")
        print(f"projected cost per run at ~8 queries/run: ${total_cost / len(rows) * 8:.4f}")
        if total_cost > 0:
            runs_per_month = 10.0 / (total_cost / len(rows) * 8)
            print(f"implied monthly run ceiling against $10 allowance: ~{runs_per_month:.0f} runs")

        hr("Exa — cost by search mode")
        for row in mode_cost_comparison(client):
            print(row)

        hr("Exa — burst of 10 requests")
        burst_test(client, n=10)


if __name__ == "__main__":
    main()
