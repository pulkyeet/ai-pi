"""Fetch, text extraction & source cache (Phase 03).

`fetch_source` is the layer's single entry point: URL in, stored deduplicated
`Source` out. See docs/execution_phases/phase-03-fetch-source-cache.md.
"""

from api.retrieval.canonical import canonicalize_url
from api.retrieval.errors import (
    FetchError,
    FetchTimeoutError,
    NoCrawlDomainError,
    ResponseTooLargeError,
    RobotsDisallowedError,
    ThinContentError,
    UnsupportedContentTypeError,
)
from api.retrieval.fetch import (
    FetchOutcome,
    HostThrottle,
    build_client,
    fetch_source,
    registrable_domain,
)
from api.retrieval.pathguess import PathGuessResult, guess_path, looks_price_shaped
from api.retrieval.robots import RobotsCache

__all__ = [
    "FetchError",
    "FetchOutcome",
    "FetchTimeoutError",
    "HostThrottle",
    "NoCrawlDomainError",
    "PathGuessResult",
    "ResponseTooLargeError",
    "RobotsCache",
    "RobotsDisallowedError",
    "ThinContentError",
    "UnsupportedContentTypeError",
    "build_client",
    "canonicalize_url",
    "fetch_source",
    "guess_path",
    "looks_price_shaped",
    "registrable_domain",
]
