"""Artifact verification — enforcing masterplan Rule 2 (§2): *an entity is
only listed if it has a verifiable public artifact*. No entity is created
without one; verification failure means the candidate is discarded and
counted, never listed with low confidence (phase doc, Design section). This
is what eliminates hallucinated competitors structurally rather than by
prompting: a hallucinated competitor has no URL that 200s, no repo that
exists, no package the registry has — it cannot survive this gate.

| Scheme | Verification | Grade |
|---|---|---|
| `web:` | HEAD/GET via `api.retrieval.fetch_source` (cached), 200 + not parked | A |
| `gh:` | GitHub API returns the repo, not 404 | A |
| `npm:` / `pypi:` | Registry API returns the package | A |
| `chrome:` | Web Store detail page resolves (best-effort — see `_verify_chrome`) | A |
| `ios:` | `itunes.apple.com/lookup` returns a result | A |
| `hf:` | HF API returns the model or space | A |
| `ph:` | Product Hunt API returns the post | B — pre-launch, no independent artifact |

Results are cached (`verification_cache`, 24h TTL, mirroring
`api.search.cache`'s reasoning): re-verifying the same candidate across runs
would waste the Phase 04 search/fetch budget for `web:` and burn free-tier
request quota for everything else. A verification failure is cached too —
a dead candidate should not be re-probed every run either.

`gh:`/`npm:`/`pypi:` verification opportunistically returns `homepage_url` /
`repository_url` pulled from the very API response that verified the
candidate — that's `api.resolve.alias`'s `gh_homepage` and
`package_repository` triggers' data, obtained for free.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx
import structlog
from pydantic import BaseModel

from api.models.entity import EntityKey, EntityScheme
from api.resolve.types import VerificationContext
from api.retrieval import FetchError, fetch_source
from api.sources.base import RetrieverUnavailableError
from api.sources.ratelimit import TokenBucket

logger = structlog.get_logger()

TTL = timedelta(hours=24)

NPM_REGISTRY = "https://registry.npmjs.org"
PYPI_REGISTRY = "https://pypi.org/pypi"
CHROME_WEBSTORE_DETAIL = "https://chromewebstore.google.com/detail"
IOS_LOOKUP = "https://itunes.apple.com/lookup"
HF_API = "https://huggingface.co/api"

# Ad hoc verification calls (not a full retriever, per-scheme constants are
# enough) still self-rate-limit, per this codebase's own convention that
# "unlimited" is never the same as "as fast as possible" (see
# `api.sources.hn`'s identical reasoning).
_RATE_PER_S = 2.0
_chrome_limiter = TokenBucket(_RATE_PER_S)
_ios_limiter = TokenBucket(_RATE_PER_S)
_hf_limiter = TokenBucket(_RATE_PER_S)
_registry_limiter = TokenBucket(_RATE_PER_S * 2)

# Soft-404 guard (Design section: "many parked domains return 200 with a
# placeholder page"). Imperfect by the phase doc's own admission — the
# residual is handled by maturity tiering (a parked domain has no other
# liveness signal, so it lands `hobby`/`abandoned`), not pretended solved.
_PARKED_KEYWORDS = (
    "domain is for sale",
    "this domain may be for sale",
    "buy this domain",
    "domain parking",
    "this domain is parked",
    "related searches",
    "checkout the full domain",
)
# Deliberately well below `api.retrieval.fetch`'s own `THIN_CONTENT_CHARS`
# (200): that gate already rejects anything shorter, so this one only
# matters if a caller ever bypasses it — it must never be stricter than the
# gate real pages already cleared to reach here, or genuinely real (if
# terse) pages would be misclassified as parked.
MIN_MEANINGFUL_CHARS = 80


def _looks_parked(text: str) -> bool:
    lowered = text.lower()
    if any(keyword in lowered for keyword in _PARKED_KEYWORDS):
        return True
    return len(text.strip()) < MIN_MEANINGFUL_CHARS


class VerificationResult(BaseModel):
    verified: bool
    grade: str
    reason: str | None = None
    homepage_url: str | None = None
    repository_url: str | None = None


def _extract_github_url(candidates: list[str | None]) -> str | None:
    for candidate in candidates:
        if candidate and "github.com" in candidate.lower():
            return candidate
    return None


async def _verify_web(ctx: VerificationContext, key: EntityKey) -> VerificationResult:
    try:
        outcome = await fetch_source(
            ctx.pool,
            ctx.http,
            ctx.throttle,
            ctx.robots,
            f"https://{key.value}",
            retrieval_reason="entity_verification",
        )
    except (FetchError, httpx.HTTPError) as exc:
        return VerificationResult(verified=False, grade="A", reason=type(exc).__name__)

    source = outcome.source
    if source.http_status != 200:
        return VerificationResult(verified=False, grade="A", reason=f"http_{source.http_status}")
    if _looks_parked(source.extracted_text or ""):
        return VerificationResult(verified=False, grade="A", reason="parked_page")
    return VerificationResult(verified=True, grade="A")


async def _verify_gh(ctx: VerificationContext, key: EntityKey) -> VerificationResult:
    if ctx.github is None:
        raise RetrieverUnavailableError("github", "no GitHubRetriever configured")
    owner, _, repo = key.value.partition("/")
    try:
        metadata = await ctx.github.repo_metadata(owner, repo)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return VerificationResult(verified=False, grade="A", reason="http_404")
        raise
    return VerificationResult(verified=True, grade="A", homepage_url=metadata.homepage)


async def _verify_npm(ctx: VerificationContext, key: EntityKey) -> VerificationResult:
    await _registry_limiter.acquire()
    resp = await ctx.http.get(f"{NPM_REGISTRY}/{quote(key.value, safe='@')}")
    if resp.status_code != 200:
        return VerificationResult(verified=False, grade="A", reason=f"http_{resp.status_code}")
    body = resp.json()
    repository = body.get("repository")
    repo_url = repository.get("url") if isinstance(repository, dict) else repository
    return VerificationResult(
        verified=True, grade="A", repository_url=_extract_github_url([repo_url])
    )


async def _verify_pypi(ctx: VerificationContext, key: EntityKey) -> VerificationResult:
    await _registry_limiter.acquire()
    resp = await ctx.http.get(f"{PYPI_REGISTRY}/{quote(key.value)}/json")
    if resp.status_code != 200:
        return VerificationResult(verified=False, grade="A", reason=f"http_{resp.status_code}")
    body = resp.json()
    project_urls: dict[str, str] = (body.get("info") or {}).get("project_urls") or {}
    repo_url = _extract_github_url(list(project_urls.values()))
    return VerificationResult(verified=True, grade="A", repository_url=repo_url)


async def _verify_chrome(ctx: VerificationContext, key: EntityKey) -> VerificationResult:
    """Best-effort: the Chrome Web Store has no documented public existence
    API. Not validated against real traffic (no Phase 01 cassette exists
    for this vendor — same honestly-flagged gap as
    `api.sources.producthunt`'s unverified GraphQL shape)."""
    await _chrome_limiter.acquire()
    resp = await ctx.http.get(f"{CHROME_WEBSTORE_DETAIL}/{key.value}")
    if resp.status_code != 200:
        return VerificationResult(verified=False, grade="A", reason=f"http_{resp.status_code}")
    if _looks_parked(resp.text):
        return VerificationResult(verified=False, grade="A", reason="listing_not_found")
    return VerificationResult(verified=True, grade="A")


async def _verify_ios(ctx: VerificationContext, key: EntityKey) -> VerificationResult:
    await _ios_limiter.acquire()
    resp = await ctx.http.get(IOS_LOOKUP, params={"id": key.value})
    resp.raise_for_status()
    body = resp.json()
    if body.get("resultCount", 0) < 1:
        return VerificationResult(verified=False, grade="A", reason="not_found")
    return VerificationResult(verified=True, grade="A")


async def _verify_hf(ctx: VerificationContext, key: EntityKey) -> VerificationResult:
    await _hf_limiter.acquire()
    for kind in ("models", "spaces"):
        resp = await ctx.http.get(f"{HF_API}/{kind}/{key.value}")
        if resp.status_code == 200:
            return VerificationResult(verified=True, grade="A")
    return VerificationResult(verified=False, grade="A", reason="not_found")


async def _verify_ph(ctx: VerificationContext, key: EntityKey) -> VerificationResult:
    if ctx.producthunt is None:
        raise RetrieverUnavailableError("producthunt", "no ProductHuntRetriever configured")
    post = await ctx.producthunt.post_by_slug(key.value)
    if post is None:
        return VerificationResult(verified=False, grade="B", reason="not_found")
    return VerificationResult(verified=True, grade="B")


_VERIFIERS = {
    EntityScheme.WEB: _verify_web,
    EntityScheme.GH: _verify_gh,
    EntityScheme.NPM: _verify_npm,
    EntityScheme.PYPI: _verify_pypi,
    EntityScheme.CHROME: _verify_chrome,
    EntityScheme.IOS: _verify_ios,
    EntityScheme.HF: _verify_hf,
    EntityScheme.PH: _verify_ph,
}


async def get_cached(ctx: VerificationContext, cache_key: str) -> VerificationResult | None:
    row = await ctx.pool.fetchrow(
        "SELECT verified, grade, reason, homepage_url, repository_url "
        "FROM verification_cache WHERE entity_key = $1 AND expires_at > now()",
        cache_key,
    )
    if row is None:
        return None
    return VerificationResult(
        verified=row["verified"],
        grade=row["grade"],
        reason=row["reason"],
        homepage_url=row["homepage_url"],
        repository_url=row["repository_url"],
    )


async def put_cached(
    ctx: VerificationContext, cache_key: str, result: VerificationResult, *, ttl: timedelta = TTL
) -> None:
    expires_at = datetime.now(UTC) + ttl
    await ctx.pool.execute(
        """
        INSERT INTO verification_cache
            (entity_key, verified, grade, reason, homepage_url, repository_url,
             checked_at, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6, now(), $7)
        ON CONFLICT (entity_key) DO UPDATE SET
            verified       = EXCLUDED.verified,
            grade          = EXCLUDED.grade,
            reason         = EXCLUDED.reason,
            homepage_url   = EXCLUDED.homepage_url,
            repository_url = EXCLUDED.repository_url,
            checked_at     = EXCLUDED.checked_at,
            expires_at     = EXCLUDED.expires_at
        """,
        cache_key,
        result.verified,
        result.grade,
        result.reason,
        result.homepage_url,
        result.repository_url,
        expires_at,
    )


async def verify_entity(ctx: VerificationContext, key: EntityKey) -> VerificationResult:
    cache_key = str(key)
    cached = await get_cached(ctx, cache_key)
    if cached is not None:
        return cached
    result = await _VERIFIERS[key.scheme](ctx, key)
    await put_cached(ctx, cache_key, result)
    return result


__all__ = [
    "MIN_MEANINGFUL_CHARS",
    "TTL",
    "VerificationResult",
    "get_cached",
    "put_cached",
    "verify_entity",
]
