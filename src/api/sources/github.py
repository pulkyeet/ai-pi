"""GitHub retriever (masterplan §5/§4.6) — "the best *structured* pain-point
source available". `is:issue label:X sort:reactions-desc` is a literal
feature-request leaderboard with reaction counts and permalinks; star
velocity over 90 days (not total stars) is the real adoption signal.

Two independent rate limits, confirmed in Phase 01 (`docs/external_apis.md`):
general REST is 5,000/hr, but the Search API (`issues_by_reactions`) is a
much stricter 30/min — a separate `TokenBucket` for each. Where GitHub
reports remaining quota in `X-RateLimit-*` headers, that's read as ground
truth on top of the local buckets.

`star_velocity_90d` needs the Starring endpoint's per-star timestamps
(`application/vnd.github.star+json`), which 403s under the current
fine-grained PAT's default "Public repositories (read-only)" scope — a real,
still-open finding from Phase 01. Rather than block on a credential upgrade
this phase can't perform, that 403 degrades to `RetrieverUnavailableError`
(a coverage gap), proven against the real recorded 403 in
`tests/fixtures/cassettes/github_api.yaml`. See `docs/tracker.md`'s
carried-forward PAT-upgrade item.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import BaseModel

from api.sources.base import RetrieverUnavailableError
from api.sources.ratelimit import TokenBucket

BASE = "https://api.github.com"

# Measured limits (docs/external_apis.md): 5,000/hr general, 30/min search.
# A hair under each so the local bucket never itself trips the real limit.
GENERAL_RATE_PER_S = 1.3
SEARCH_RATE_PER_S = 0.45

_LINK_LAST_PAGE_RE = re.compile(r"[?&]page=(\d+)>;\s*rel=\"last\"")


class GitHubRepo(BaseModel):
    full_name: str
    stargazers_count: int
    open_issues_count: int
    license: str | None = None
    last_commit_at: datetime | None = None
    contributors_count: int | None = None
    # Phase 07's gh_homepage alias-merge trigger reads this off a call
    # `repo_metadata` already makes for verification — no extra request.
    homepage: str | None = None


class GitHubIssue(BaseModel):
    number: int
    title: str
    html_url: str
    reactions_total: int
    repo: str


def compute_star_velocity(
    starred_ats: list[str], *, now: datetime | None = None, window_days: int = 90
) -> float:
    """Stars gained per day, averaged over the stars that fall inside the
    trailing `window_days`. `0.0` if the repo has no stars in that window
    (including no stars at all)."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=window_days)
    timestamps = [datetime.fromisoformat(s.replace("Z", "+00:00")) for s in starred_ats]
    recent = [d for d in timestamps if d >= cutoff]
    if not recent:
        return 0.0
    span_days = max(1.0, (now - min(recent)).total_seconds() / 86400)
    return len(recent) / span_days


class GitHubRetriever:
    name = "github"
    grade = "A"  # structured API metadata; issue *comments* would grade D (masterplan §4.6)

    def __init__(self, client: httpx.AsyncClient, token: str) -> None:
        self._client = client
        self._token = token
        self._general_limiter = TokenBucket(GENERAL_RATE_PER_S, capacity=5)
        self._search_limiter = TokenBucket(SEARCH_RATE_PER_S, capacity=1)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/vnd.github+json"}

    def _raise_for_rate_limit(self, resp: httpx.Response) -> None:
        if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
            raise RetrieverUnavailableError(
                self.name, "rate limit exhausted (x-ratelimit-remaining=0)"
            )

    async def repo_metadata(self, owner: str, repo: str) -> GitHubRepo:
        await self._general_limiter.acquire()
        resp = await self._client.get(f"{BASE}/repos/{owner}/{repo}", headers=self._headers())
        self._raise_for_rate_limit(resp)
        resp.raise_for_status()
        body = resp.json()
        return GitHubRepo(
            full_name=body["full_name"],
            stargazers_count=body["stargazers_count"],
            open_issues_count=body["open_issues_count"],
            license=(body.get("license") or {}).get("spdx_id"),
            last_commit_at=body.get("pushed_at"),
            contributors_count=await self._contributors_count(owner, repo),
            homepage=body.get("homepage") or None,
        )

    async def _contributors_count(self, owner: str, repo: str) -> int | None:
        await self._general_limiter.acquire()
        resp = await self._client.get(
            f"{BASE}/repos/{owner}/{repo}/contributors",
            headers=self._headers(),
            params={"per_page": 1, "anon": "true"},
        )
        if resp.status_code != 200:
            return None
        link = resp.headers.get("link", "")
        match = _LINK_LAST_PAGE_RE.search(link)
        if match:
            return int(match.group(1))
        return len(resp.json())

    async def issues_by_reactions(
        self, owner: str, repo: str, *, label: str, limit: int = 5
    ) -> list[GitHubIssue]:
        await self._search_limiter.acquire()
        resp = await self._client.get(
            f"{BASE}/search/issues",
            headers=self._headers(),
            params={
                "q": f"repo:{owner}/{repo} is:issue label:{label}",
                "sort": "reactions",
                "order": "desc",
                "per_page": limit,
            },
        )
        self._raise_for_rate_limit(resp)
        resp.raise_for_status()
        body = resp.json()
        return [
            GitHubIssue(
                number=item["number"],
                title=item["title"],
                html_url=item["html_url"],
                reactions_total=item.get("reactions", {}).get("total_count", 0),
                repo=f"{owner}/{repo}",
            )
            for item in body.get("items", [])
        ]

    async def star_velocity_90d(self, owner: str, repo: str, *, per_page: int = 100) -> float:
        await self._general_limiter.acquire()
        resp = await self._client.get(
            f"{BASE}/repos/{owner}/{repo}/stargazers",
            headers={**self._headers(), "Accept": "application/vnd.github.star+json"},
            params={"per_page": per_page},
        )
        if resp.status_code == 403:
            raise RetrieverUnavailableError(
                self.name,
                "Starring endpoint returned 403 - PAT lacks Starring permission "
                "(needs a classic PAT or an explicit fine-grained Starring scope)",
            )
        self._raise_for_rate_limit(resp)
        resp.raise_for_status()
        stars = resp.json()
        starred_ats = [s["starred_at"] for s in stars if "starred_at" in s]
        return compute_star_velocity(starred_ats)


__all__ = ["GitHubIssue", "GitHubRepo", "GitHubRetriever", "compute_star_velocity"]
