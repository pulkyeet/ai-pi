"""Deterministic path guessing — the cost lever: fetch a known page directly
instead of searching for it (masterplan §7).

Ordered by observed real-world frequency (Phase 01: `/pricing` cleared 33/40
of the corpus on its own). Capped at `MAX_ATTEMPTS_PER_DOMAIN` and only ever
probes registrable-domain roots — never a subpath, so a guess never reaches
into an unrelated tenant on a shared host.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import asyncpg
import httpx
import structlog

from api.retrieval import cache as source_cache
from api.retrieval.errors import FetchError
from api.retrieval.fetch import NEGATIVE_TTL, POSITIVE_TTL, HostThrottle, fetch_source
from api.retrieval.robots import RobotsCache

logger = structlog.get_logger()

MAX_ATTEMPTS_PER_DOMAIN = 4

PRICING_PATHS: tuple[str, ...] = (
    "/pricing",
    "/plans",
    "/pricing-plans",
    "/price",
    "/pricing/",
    "/plans-and-pricing",
    "/subscribe",
)
DOCS_PATHS: tuple[str, ...] = ("/docs", "/documentation", "/developers", "/api")
CHANGELOG_PATHS: tuple[str, ...] = ("/changelog", "/releases", "/whats-new", "/blog/changelog")

PATH_CANDIDATES: dict[str, tuple[str, ...]] = {
    "pricing": PRICING_PATHS,
    "docs": DOCS_PATHS,
    "changelog": CHANGELOG_PATHS,
}

# Deliberately loose: this is a routing heuristic that decides whether to
# treat a page as "found the pricing page", not extraction — Phase 06 decides
# what any number actually means.
_CURRENCY = r"[$€£¥₹]\s?\d"
_PER_PERIOD = r"(?:per\s+(?:mo|month|seat|user|year)\b|/\s?(?:mo|month)\b)"
_FREE = r"\bfree\b"
PRICE_TOKEN_RE = re.compile(f"({_CURRENCY}|{_PER_PERIOD}|{_FREE})", re.IGNORECASE)


def looks_price_shaped(text: str) -> bool:
    return bool(PRICE_TOKEN_RE.search(text))


def candidate_paths(kind: str, *, limit: int = MAX_ATTEMPTS_PER_DOMAIN) -> tuple[str, ...]:
    return PATH_CANDIDATES[kind][:limit]


@dataclass(frozen=True)
class PathGuessResult:
    found_path: str | None
    from_cache: bool


async def guess_path(
    pool: asyncpg.Pool,
    client: httpx.AsyncClient,
    throttle: HostThrottle,
    robots: RobotsCache,
    root_key: str,
    kind: str,
    *,
    retrieval_reason: str,
) -> PathGuessResult:
    """Try `kind`'s candidate paths against `root_key` in order, stopping at
    the first success. Both the resolution (positive 7d / negative 24h) and
    every individual attempt (for the aggregate hit-rate measurement) are
    persisted, so a re-run within TTL makes zero guessing requests."""
    cached = await source_cache.get_path_guess(pool, root_key, kind)
    if cached is not None:
        return PathGuessResult(found_path=cached.found_path, from_cache=True)

    for path in candidate_paths(kind):
        url = f"https://{root_key}{path}"
        try:
            outcome = await fetch_source(
                pool, client, throttle, robots, url, retrieval_reason=retrieval_reason
            )
        except FetchError:
            await source_cache.record_path_guess_attempt(pool, root_key, kind, path, outcome="miss")
            continue

        text = outcome.source.extracted_text or ""
        success = outcome.source.http_status == 200 and (
            looks_price_shaped(text) if kind == "pricing" else bool(text)
        )
        await source_cache.record_path_guess_attempt(
            pool, root_key, kind, path, outcome="hit" if success else "miss"
        )
        if success:
            await source_cache.upsert_path_guess(
                pool, root_key, kind, found_path=path, ttl=POSITIVE_TTL
            )
            return PathGuessResult(found_path=path, from_cache=False)

    await source_cache.upsert_path_guess(pool, root_key, kind, found_path=None, ttl=NEGATIVE_TTL)
    return PathGuessResult(found_path=None, from_cache=False)
