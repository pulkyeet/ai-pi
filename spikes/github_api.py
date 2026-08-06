"""Phase 01 spike: GitHub REST API with a fine-grained PAT.

Verifies: 5,000 req/hr authenticated rate limit, issue search sorted by
reaction count (masterplan's community-signal query shape), and that
per-star timestamps are retrievable for 90-day velocity computation.

Requires GITHUB_TOKEN in the environment (see .env).

Run: uv run python spikes/github_api.py
"""

from __future__ import annotations

import os

import httpx

from _common import hr, timed_runs, vcr_cassette

BASE = "https://api.github.com"
REPO = "microsoft/vscode"


def auth_headers() -> dict[str, str]:
    token = os.environ["GITHUB_TOKEN"]
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def rate_limit(client: httpx.Client) -> httpx.Response:
    return client.get(f"{BASE}/rate_limit", headers=auth_headers())


def search_issues_by_reactions(client: httpx.Client) -> httpx.Response:
    return client.get(
        f"{BASE}/search/issues",
        headers=auth_headers(),
        params={
            "q": f"repo:{REPO} is:issue label:feature-request",
            "sort": "reactions",
            "order": "desc",
            "per_page": 5,
        },
    )


def star_history_sample(client: httpx.Client) -> httpx.Response:
    return client.get(
        f"{BASE}/repos/{REPO}/stargazers",
        headers={**auth_headers(), "Accept": "application/vnd.github.star+json"},
        params={"per_page": 10},
    )


def main() -> None:
    with vcr_cassette("github_api"), httpx.Client(timeout=15) as client:
        hr("GitHub — rate limit (PAT)")
        r = rate_limit(client)
        core = r.json()["resources"]["core"]
        print(f"status={r.status_code} limit={core['limit']} remaining={core['remaining']}")
        assert core["limit"] == 5000, f"expected 5000 req/hr, got {core['limit']}"

        hr("GitHub — issue search sorted by reactions")
        responses, stats = timed_runs(lambda: search_issues_by_reactions(client), n=3)
        r2 = responses[-1]
        body = r2.json()
        print(f"status={r2.status_code} total_count={body.get('total_count')}")
        print(f"latency p50={stats['p50_ms']:.0f}ms p95={stats['p95_ms']:.0f}ms")
        if body.get("items"):
            top = body["items"][0]
            print(
                f"top issue: {top['title']!r} reactions.total_count="
                f"{top.get('reactions', {}).get('total_count')}"
            )

        hr("GitHub — star history (per-star timestamps for velocity)")
        r3 = star_history_sample(client)
        body3 = r3.json()
        if r3.status_code == 403:
            print(
                f"status=403 BLOCKED: {body3.get('message')!r} — a fine-grained PAT's default "
                "'Public repositories (read-only)' access does NOT cover the Starring endpoint, "
                "on REST *or* GraphQL, even though star data is public. Needs either a classic "
                "PAT or an explicit 'Starring' repo permission on the fine-grained token."
            )
        elif isinstance(body3, list) and body3 and "starred_at" in body3[0]:
            print(f"status=200 sample_size={len(body3)} first starred_at={body3[0]['starred_at']}")
        else:
            print(f"status={r3.status_code} unexpected shape: {str(body3)[:200]}")


if __name__ == "__main__":
    main()
