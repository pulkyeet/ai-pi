"""Shared helpers for Phase 01 spike scripts. Throwaway — not imported by src/api.

Every spike that hits a real HTTP API should record its traffic through
`vcr_cassette()` so the run becomes a replayable fixture for later phases.
"""

from __future__ import annotations

import re
import statistics
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar

import vcr

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
CASSETTES_DIR = FIXTURES_DIR / "cassettes"

# Header names to strip entirely, and query-param names to redact, before a
# cassette is written. Matched case-insensitively.
SECRET_HEADERS = {"authorization", "x-api-key", "x-apikey", "api-key", "cookie", "set-cookie"}
SECRET_QUERY_PARAMS = {"api_key", "apikey", "key", "token", "access_token"}

_KEY_SHAPED = re.compile(r"(bearer\s+[a-z0-9._-]{16,}|sk-[a-z0-9]{16,})", re.IGNORECASE)


def _scrub_headers(request: Any) -> Any:
    for header in list(request.headers):
        if header.lower() in SECRET_HEADERS:
            request.headers[header] = "REDACTED"
    return request


def _scrub_response_headers(response: dict[str, Any]) -> dict[str, Any]:
    headers = response.get("headers", {})
    for header in list(headers):
        if header.lower() in SECRET_HEADERS:
            headers[header] = ["REDACTED"]
    return response


def make_vcr() -> vcr.VCR:
    return vcr.VCR(
        cassette_library_dir=str(CASSETTES_DIR),
        filter_headers=[(h, "REDACTED") for h in SECRET_HEADERS],
        filter_query_parameters=[(p, "REDACTED") for p in SECRET_QUERY_PARAMS],
        before_record_request=_scrub_headers,
        before_record_response=_scrub_response_headers,
        decode_compressed_response=True,
    )


@contextmanager
def vcr_cassette(name: str, record_mode: str = "once") -> Iterator[None]:
    """Record/replay HTTP traffic for one spike into tests/fixtures/cassettes/<name>.yaml."""
    my_vcr = make_vcr()
    with my_vcr.use_cassette(f"{name}.yaml", record_mode=record_mode):
        yield


T = TypeVar("T")


def timed_runs(fn: Callable[[], T], n: int = 3) -> tuple[list[T], dict[str, float]]:
    """Call fn() n times, return results plus p50/p95 latency in ms."""
    results: list[T] = []
    latencies_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        results.append(fn())
        latencies_ms.append((time.perf_counter() - start) * 1000)
    latencies_ms.sort()
    return results, {
        "p50_ms": statistics.median(latencies_ms),
        "p95_ms": latencies_ms[min(len(latencies_ms) - 1, int(len(latencies_ms) * 0.95))],
        "min_ms": latencies_ms[0],
        "max_ms": latencies_ms[-1],
        "n": float(n),
    }


def has_key_shaped_secret(text: str) -> bool:
    return bool(_KEY_SHAPED.search(text))


def hr(title: str) -> None:
    print(f"\n{'=' * 10} {title} {'=' * 10}")
