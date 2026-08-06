"""HTTP fetching, per-host politeness, conditional requests, and the top-level
`fetch_source` entry point: URL -> stored, deduplicated, plain-text `Source`.

Retry reuses Phase 02's policy (`api.executor.retry`) unmodified: 429/5xx/
timeout only, full-jitter backoff, `Retry-After` honoured when present.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

import asyncpg
import httpx
import structlog
import tldextract

from api.executor.retry import MAX_ATTEMPTS, RETRYABLE_STATUS_CODES, backoff_delay
from api.models.source import CacheOutcome, Source
from api.retrieval import cache as source_cache
from api.retrieval.canonical import canonicalize_url
from api.retrieval.errors import (
    FetchTimeoutError,
    NoCrawlDomainError,
    ResponseTooLargeError,
    RobotsDisallowedError,
    ThinContentError,
    UnsupportedContentTypeError,
)
from api.retrieval.extract_text import extract
from api.retrieval.robots import RobotsCache, is_no_crawl_domain

logger = structlog.get_logger()

USER_AGENT = "AIProductInvestigatorBot/0.1 (+mailto:pulkyeet@gmail.com)"

CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 15.0
TOTAL_TIMEOUT_S = 20.0
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 5 * 1024 * 1024

POSITIVE_TTL = timedelta(days=7)
NEGATIVE_TTL = timedelta(hours=24)

PER_HOST_CONCURRENCY = 2
PER_HOST_MIN_GAP_S = 0.25

THIN_CONTENT_CHARS = 200
_CONSENT_KEYWORDS = (
    "accept cookies",
    "cookie consent",
    "we use cookies",
    "manage preferences",
    "accept all cookies",
)

ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml")


def registrable_domain(host: str) -> str:
    ext = tldextract.extract(host)
    return f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain


def build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=CONNECT_TIMEOUT_S,
            read=READ_TIMEOUT_S,
            write=READ_TIMEOUT_S,
            pool=READ_TIMEOUT_S,
        ),
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        headers={"User-Agent": USER_AGENT},
    )


class HostThrottle:
    """At most `concurrency` in-flight requests and a minimum `min_gap_s` gap
    between request starts, per host — independent of any global crawl
    semaphore. Without this, path-guessing's handful of candidates against
    one domain looks exactly like a scraper."""

    def __init__(
        self, *, concurrency: int = PER_HOST_CONCURRENCY, min_gap_s: float = PER_HOST_MIN_GAP_S
    ) -> None:
        self._concurrency = concurrency
        self._min_gap_s = min_gap_s
        self._semaphores: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(concurrency)
        )
        self._gap_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_start: dict[str, float] = {}

    @asynccontextmanager
    async def acquire(self, host: str) -> AsyncIterator[None]:
        async with self._semaphores[host]:
            async with self._gap_locks[host]:
                last = self._last_start.get(host)
                if last is not None:
                    wait = self._min_gap_s - (time.monotonic() - last)
                    if wait > 0:
                        await asyncio.sleep(wait)
                self._last_start[host] = time.monotonic()
            yield


@dataclass
class FetchedResponse:
    status_code: int
    headers: httpx.Headers
    body: bytes
    encoding: str
    final_url: str


async def _fetch_once(
    client: httpx.AsyncClient, url: str, *, etag: str | None, last_modified: str | None
) -> FetchedResponse:
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    async with client.stream("GET", url, headers=headers) as resp:
        body = bytearray()
        async for chunk in resp.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ResponseTooLargeError(url)
        return FetchedResponse(
            status_code=resp.status_code,
            headers=resp.headers,
            body=bytes(body),
            encoding=resp.charset_encoding or "utf-8",
            final_url=str(resp.url),
        )


def _retry_after_delay(resp: FetchedResponse) -> float | None:
    value = resp.headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


async def _fetch_with_retries(
    client: httpx.AsyncClient,
    throttle: HostThrottle,
    url: str,
    *,
    etag: str | None,
    last_modified: str | None,
) -> FetchedResponse:
    host = httpx.URL(url).host
    last_error: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            async with throttle.acquire(host):
                resp = await asyncio.wait_for(
                    _fetch_once(client, url, etag=etag, last_modified=last_modified),
                    timeout=TOTAL_TIMEOUT_S,
                )
        except (httpx.TimeoutException, TimeoutError) as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(backoff_delay(attempt))
            continue

        if resp.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_ATTEMPTS - 1:
            delay = _retry_after_delay(resp)
            await asyncio.sleep(delay if delay is not None else backoff_delay(attempt))
            continue

        return resp

    raise FetchTimeoutError(url) from last_error


def _looks_like_consent_wall(text: str, html: str) -> bool:
    if len(text) >= THIN_CONTENT_CHARS:
        return False
    haystack = (text + " " + html[:2000]).lower()
    return any(keyword in haystack for keyword in _CONSENT_KEYWORDS)


@dataclass
class FetchOutcome:
    source: Source
    cache_outcome: CacheOutcome


async def fetch_source(
    pool: asyncpg.Pool,
    client: httpx.AsyncClient,
    throttle: HostThrottle,
    robots: RobotsCache,
    url: str,
    *,
    retrieval_reason: str,
    force: bool = False,
) -> FetchOutcome:
    """Turn a URL into a stored, deduplicated `Source`.

    Raises a typed `api.retrieval.errors.FetchError` subclass when nothing
    cacheable came back (robots/no-crawl/too-large/wrong-content-type/thin
    content/timeout). Ordinary non-2xx HTTP responses are not errors — they
    are cached like any other outcome and returned normally.
    """
    canonical = canonicalize_url(url)
    host = httpx.URL(canonical).host
    root_key = registrable_domain(host)

    if not force:
        cached = await source_cache.get_fresh(pool, canonical)
        if cached is not None:
            return FetchOutcome(cached, CacheOutcome.HIT)

    if is_no_crawl_domain(host):
        raise NoCrawlDomainError(canonical)

    if not await robots.is_allowed(canonical, USER_AGENT):
        raise RobotsDisallowedError(canonical)

    stale = await source_cache.get_any(pool, canonical)
    etag = stale.etag if stale else None
    last_modified = stale.last_modified if stale else None

    resp = await _fetch_with_retries(
        client, throttle, canonical, etag=etag, last_modified=last_modified
    )

    # Rule 2 (canonicalisation §2): don't assume https, but adopt it once a
    # redirect has actually proven the host serves it — recorded, not guessed.
    final_scheme = httpx.URL(resp.final_url).scheme
    if final_scheme == "https" and canonical.startswith("http://"):
        upgraded = canonicalize_url(canonical, force_https=True)
        logger.debug("fetch.observed_https_redirect", original=canonical, upgraded=upgraded)
        canonical = upgraded

    if resp.status_code == 304 and stale is not None:
        refreshed = await source_cache.refresh_ttl(pool, canonical, ttl=POSITIVE_TTL)
        return FetchOutcome(refreshed, CacheOutcome.CONDITIONAL_304)

    if resp.status_code != 200:
        source = await source_cache.upsert(
            pool,
            canonical_url=canonical,
            root_key=root_key,
            http_status=resp.status_code,
            extracted_text=None,
            content_hash=None,
            etag=None,
            last_modified=None,
            retrieval_reason=retrieval_reason,
            ttl=NEGATIVE_TTL,
        )
        return FetchOutcome(source, CacheOutcome.MISS)

    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type and not any(content_type == ct for ct in ALLOWED_CONTENT_TYPES):
        raise UnsupportedContentTypeError(canonical, content_type)

    html = resp.body.decode(resp.encoding, errors="replace")
    text = extract(html, url=canonical)

    if text is None:
        raise ThinContentError(canonical)
    if _looks_like_consent_wall(text, html):
        raise ThinContentError(canonical)

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    source = await source_cache.upsert(
        pool,
        canonical_url=canonical,
        root_key=root_key,
        http_status=200,
        extracted_text=text,
        content_hash=content_hash,
        etag=resp.headers.get("etag"),
        last_modified=resp.headers.get("last-modified"),
        retrieval_reason=retrieval_reason,
        ttl=POSITIVE_TTL,
    )
    return FetchOutcome(source, CacheOutcome.MISS)


__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "MAX_RESPONSE_BYTES",
    "NEGATIVE_TTL",
    "POSITIVE_TTL",
    "USER_AGENT",
    "FetchOutcome",
    "FetchedResponse",
    "HostThrottle",
    "build_client",
    "fetch_source",
    "registrable_domain",
]
