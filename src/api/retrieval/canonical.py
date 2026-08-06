"""URL canonicalisation — the cache key for `sources.canonical_url`.

Must be stable and idempotent: `canonicalize_url(canonicalize_url(u)) ==
canonicalize_url(u)` for any `u`, or the source cache silently accumulates
duplicates and the shared-cache-across-users cost saving (masterplan §8.2)
evaporates. See `tests/unit/test_canonical.py` for the property test.

Scheme upgrade (rule 2 — force https where the host actually redirects to
it) is deliberately *not* done here: that requires observing a real redirect,
which this pure function cannot do without a network call. `api.retrieval.fetch`
re-canonicalises the final, post-redirect URL instead.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

import structlog

logger = structlog.get_logger()

_DEFAULT_PORTS = {"http": 80, "https": 443}

# Denylist, not allowlist: an allowlist of "safe" query params would be safer
# but unmaintainable against the long tail of real marketing URLs. Every
# removal is logged so a canonicalisation bug (raw URL 200s, canonical 404s)
# is findable.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_EXACT = frozenset({"fbclid", "gclid", "ref", "source", "mc_cid", "mc_eid"})

_PCT_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def _is_tracking_param(key: str) -> bool:
    return key in _TRACKING_EXACT or any(key.startswith(p) for p in _TRACKING_PREFIXES)


def _decode_pct(match: re.Match[str]) -> str:
    hex_pair = match.group(0)[1:]
    char = chr(int(hex_pair, 16))
    return char if char in _UNRESERVED else "%" + hex_pair.upper()


def _renormalise_percent_encoding(component: str) -> str:
    """Uppercase the hex of every existing `%XX` escape, and decode only the
    ones that spell an RFC 3986 *unreserved* character. Reserved characters
    (e.g. `%2F`, a literal slash inside one path segment) must stay encoded —
    decoding them would change how the path splits into segments."""
    return _PCT_RE.sub(_decode_pct, component)


def canonicalize_url(url: str, *, force_https: bool = False) -> str:
    parts = urlsplit(url.strip())
    scheme = "https" if force_https else parts.scheme.lower()
    host = (parts.hostname or "").lower()

    netloc = host
    port = parts.port
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"
    if parts.username:
        userinfo = parts.username + (f":{parts.password}" if parts.password else "")
        netloc = f"{userinfo}@{netloc}"

    path = parts.path or "/"
    path = _renormalise_percent_encoding(path)
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"

    kept: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if _is_tracking_param(key):
            logger.debug("canonical_url.stripped_tracking_param", key=key, url=url)
            continue
        kept.append((key, value))
    kept.sort(key=lambda kv: kv[0])
    query = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in kept)

    return urlunsplit((scheme, netloc, path, query, ""))
