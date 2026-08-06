"""robots.txt compliance and the hardcoded aggregator no-crawl set.

Fetched once per host, cached 24h, parsed with `urllib.robotparser`. A
fetch failure (host down, non-200) is treated as allow-all — the common
crawler convention, and the alternative (treat as disallow) would silently
starve every task whose target host's robots.txt is briefly unreachable.
"""

from __future__ import annotations

import time
from urllib.robotparser import RobotFileParser

import httpx
import structlog

logger = structlog.get_logger()

ROBOTS_CACHE_TTL_S = 24 * 3600.0

# Aggregator sites with heavy anti-bot measures are never crawled at all
# (masterplan §5) — read via SERP snippets in Phase 04 instead. Enforced here
# independently of what their robots.txt happens to say.
NO_CRAWL_DOMAINS: frozenset[str] = frozenset({"g2.com", "capterra.com"})

_ALLOW_ALL_ROBOTS_TXT = ["User-agent: *", "Allow: /"]


def is_no_crawl_domain(host: str) -> bool:
    host = host.lower()
    return host in NO_CRAWL_DOMAINS or any(
        host.endswith(f".{domain}") for domain in NO_CRAWL_DOMAINS
    )


class RobotsCache:
    def __init__(self, client: httpx.AsyncClient, *, ttl_s: float = ROBOTS_CACHE_TTL_S) -> None:
        self._client = client
        self._ttl_s = ttl_s
        self._parsers: dict[str, tuple[float, RobotFileParser]] = {}

    async def _get_parser(self, scheme: str, host: str) -> RobotFileParser:
        cached = self._parsers.get(host)
        now = time.monotonic()
        if cached is not None and now - cached[0] < self._ttl_s:
            return cached[1]

        parser = RobotFileParser()
        robots_url = f"{scheme}://{host}/robots.txt"
        try:
            resp = await self._client.get(robots_url, timeout=5.0)
            if resp.status_code == 200:
                parser.parse(resp.text.splitlines())
            else:
                parser.parse(_ALLOW_ALL_ROBOTS_TXT)
        except httpx.HTTPError:
            logger.warning("robots.fetch_failed", host=host)
            parser.parse(_ALLOW_ALL_ROBOTS_TXT)

        self._parsers[host] = (now, parser)
        return parser

    async def is_allowed(self, url: str, user_agent: str) -> bool:
        parsed = httpx.URL(url)
        host = parsed.host
        if is_no_crawl_domain(host):
            return False
        parser = await self._get_parser(parsed.scheme, host)
        return parser.can_fetch(user_agent, url)
