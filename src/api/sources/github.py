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
(`application/vnd.github.star+json`), but GitHub restricted that endpoint on
2026-06-30 to repo **admins and collaborators only** — and fine-grained PATs
are not supported for it at all (no fine-grained permission exists; a classic
PAT with `public_repo` works only when the token's owner *is* an admin or
collaborator of the target repo). We are never that for competitor repos, so
`star_velocity_90d` is effectively dead for arbitrary repositories; its 403
degrades to `RetrieverUnavailableError` (a coverage gap), proven against the
real recorded 403 in `tests/fixtures/cassettes/github_api.yaml`. Only the
total `stargazers_count` (via `repo_metadata`) remains readable. See
`docs/external_apis.md`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
from pydantic import BaseModel

from api.sources.base import RetrieverUnavailableError
from api.sources.cache import cache_key, get_fresh, upsert
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


class GitHubRepoSearchHit(BaseModel):
    full_name: str
    html_url: str
    description: str | None = None
    stargazers_count: int


_ISSUE_REPO_RE = re.compile(r"github\.com/([^/]+/[^/]+)/issues/\d+")


def _repo_from_issue_url(html_url: str) -> str:
    match = _ISSUE_REPO_RE.search(html_url)
    return match.group(1) if match else ""


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

    def __init__(
        self,
        client: httpx.AsyncClient,
        token: str,
        *,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        self._client = client
        self._token = token
        self._pool = pool
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
        key = cache_key(self.name, "repo_metadata", owner, repo)
        cached = await self._cached_search(key)
        if cached is not None:
            return GitHubRepo.model_validate(cached[0])
        await self._general_limiter.acquire()
        resp = await self._client.get(f"{BASE}/repos/{owner}/{repo}", headers=self._headers())
        self._raise_for_rate_limit(resp)
        resp.raise_for_status()
        body = resp.json()
        metadata = GitHubRepo(
            full_name=body["full_name"],
            stargazers_count=body["stargazers_count"],
            open_issues_count=body["open_issues_count"],
            license=(body.get("license") or {}).get("spdx_id"),
            last_commit_at=body.get("pushed_at"),
            contributors_count=await self._contributors_count(owner, repo),
            homepage=body.get("homepage") or None,
        )
        await self._store_search(key, [metadata.model_dump(mode="json")])
        return metadata

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

    async def _cached_search(self, key: str) -> list[dict[str, Any]] | None:
        if self._pool is None:
            return None
        return await get_fresh(self._pool, key)

    async def _store_search(self, key: str, payload: list[dict[str, Any]]) -> None:
        if self._pool is not None:
            await upsert(self._pool, key=key, provider=self.name, payload=payload)

    async def issues_by_reactions(
        self, owner: str, repo: str, *, label: str, limit: int = 5
    ) -> list[GitHubIssue]:
        key = cache_key(self.name, "issues_by_reactions", f"{owner}/{repo}", label, str(limit))
        cached = await self._cached_search(key)
        if cached is not None:
            return [GitHubIssue.model_validate(item) for item in cached]
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
        issues = [
            GitHubIssue(
                number=item["number"],
                title=item["title"],
                html_url=item["html_url"],
                reactions_total=item.get("reactions", {}).get("total_count", 0),
                repo=f"{owner}/{repo}",
            )
            for item in body.get("items", [])
        ]
        await self._store_search(key, [i.model_dump(mode="json") for i in issues])
        return issues

    async def search_issues(
        self, query: str, *, label: str | None = None, limit: int = 10
    ) -> list[GitHubIssue]:
        """Repo-agnostic issue search across all of GitHub — for
        `mine_community`'s `github` venue (Phase 10), distinct from
        `issues_by_reactions` above, which is scoped to one already-known
        repo (`oss_profile`'s use case). Masterplan §5's leaderboard query
        (`is:issue label:enhancement sort:reactions-desc`) is illustrated
        without a `repo:` qualifier, i.e. a category-wide search."""
        key = cache_key(self.name, "search_issues", query, label or "", str(limit))
        cached = await self._cached_search(key)
        if cached is not None:
            return [GitHubIssue.model_validate(item) for item in cached]
        await self._search_limiter.acquire()
        q = f"{query} is:issue"
        if label:
            q += f" label:{label}"
        resp = await self._client.get(
            f"{BASE}/search/issues",
            headers=self._headers(),
            params={"q": q, "sort": "reactions", "order": "desc", "per_page": limit},
        )
        self._raise_for_rate_limit(resp)
        resp.raise_for_status()
        body = resp.json()
        issues = [
            GitHubIssue(
                number=item["number"],
                title=item["title"],
                html_url=item["html_url"],
                reactions_total=item.get("reactions", {}).get("total_count", 0),
                repo=_repo_from_issue_url(item["html_url"]),
            )
            for item in body.get("items", [])
        ]
        await self._store_search(key, [i.model_dump(mode="json") for i in issues])
        return issues

    async def search_repositories(
        self, query: str, *, limit: int = 10
    ) -> list[GitHubRepoSearchHit]:
        """Repository search — masterplan §5's `awesome-<category>` curated-list
        seeding, "very high precision seeds", for `discover_competitors`."""
        key = cache_key(self.name, "search_repositories", query, str(limit))
        cached = await self._cached_search(key)
        if cached is not None:
            return [GitHubRepoSearchHit.model_validate(item) for item in cached]
        await self._search_limiter.acquire()
        resp = await self._client.get(
            f"{BASE}/search/repositories",
            headers=self._headers(),
            params={"q": query, "sort": "stars", "order": "desc", "per_page": limit},
        )
        self._raise_for_rate_limit(resp)
        resp.raise_for_status()
        body = resp.json()
        hits = [
            GitHubRepoSearchHit(
                full_name=item["full_name"],
                html_url=item["html_url"],
                description=item.get("description"),
                stargazers_count=item.get("stargazers_count", 0),
            )
            for item in body.get("items", [])
        ]
        await self._store_search(key, [h.model_dump(mode="json") for h in hits])
        return hits

    async def star_velocity_90d(self, owner: str, repo: str, *, per_page: int = 100) -> float:
        key = cache_key(self.name, "star_velocity_90d", owner, repo, str(per_page))
        cached = await self._cached_search(key)
        if cached is not None:
            if cached[0].get("unavailable"):
                raise RetrieverUnavailableError(
                    self.name, "starring endpoint unavailable (cached response)"
                )
            return float(cached[0]["velocity"])
        await self._general_limiter.acquire()
        resp = await self._client.get(
            f"{BASE}/repos/{owner}/{repo}/stargazers",
            headers={**self._headers(), "Accept": "application/vnd.github.star+json"},
            params={"per_page": per_page},
        )
        if resp.status_code == 403:
            await self._store_search(key, [{"unavailable": True}])
            raise RetrieverUnavailableError(
                self.name,
                "Starring endpoint returned 403 - restricted to repo admins/collaborators "
                "since 2026-06-30; fine-grained PATs are not supported for it at all, and a "
                "classic PAT works only if its owner is an admin/collaborator of the target repo",
            )
        self._raise_for_rate_limit(resp)
        resp.raise_for_status()
        stars = resp.json()
        starred_ats = [s["starred_at"] for s in stars if "starred_at" in s]
        velocity = compute_star_velocity(starred_ats)
        await self._store_search(key, [{"velocity": velocity}])
        return velocity


__all__ = ["GitHubIssue", "GitHubRepo", "GitHubRetriever", "compute_star_velocity"]
