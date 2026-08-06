"""Typed failure classes for the retrieval layer.

A raised error here means `fetch_source` produced no usable `Source` row.
Ordinary non-2xx HTTP outcomes (404, 5xx exhausted, etc.) are *not* errors —
they are cached and returned like any other `Source` (masterplan: dead links
must not be re-fetched every run). These types cover the cases where nothing
worth caching came back at all.
"""

from __future__ import annotations


class FetchError(Exception):
    def __init__(self, url: str, message: str | None = None) -> None:
        super().__init__(message or url)
        self.url = url


class RobotsDisallowedError(FetchError):
    """robots.txt forbids this URL for our user agent."""


class NoCrawlDomainError(FetchError):
    """Hardcoded no-crawl set (aggregators with heavy anti-bot measures),
    enforced independently of what robots.txt says."""


class ResponseTooLargeError(FetchError):
    """Response exceeded the streamed size cap; aborted mid-transfer."""


class UnsupportedContentTypeError(FetchError):
    def __init__(self, url: str, content_type: str) -> None:
        super().__init__(url, f"unsupported content type {content_type!r} for {url}")
        self.content_type = content_type


class ThinContentError(FetchError):
    """Extracted text is under the minimum-content threshold and the raw page
    looks like a cookie wall / consent interstitial rather than a real miss."""


class FetchTimeoutError(FetchError):
    """No response was ever obtained after exhausting retries."""
