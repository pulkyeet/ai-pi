"""Secret-scrubbing VCR factory for Phase 04 integration tests — mirrors
`spikes/_common.py`'s `make_vcr()` (redacted headers/query params,
`decode_compressed_response`) but lives as its own module: the `TID251`
ruff rule bans `import spikes` from anywhere outside `spikes/` itself (see
`pyproject.toml`), and that lint runs over `tests/` too (`Makefile`'s `fmt`/
`check` targets scope to `src tests migrations`).

Phase 01's cassettes were all recorded with the default `match_on` (method,
scheme, host, port, path, query) — sufficient for every GET-based vendor
here. Exa is POST-with-a-JSON-body, where the default matcher ignores body
entirely, so `replay_cassette` accepts an explicit `match_on` to add `body`
for that one case.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import vcr

CASSETTES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "cassettes"

SECRET_HEADERS = {"authorization", "x-api-key", "x-apikey", "api-key", "cookie", "set-cookie"}
SECRET_QUERY_PARAMS = {"api_key", "apikey", "key", "token", "access_token"}


def _scrub_response_headers(response: dict[str, Any]) -> dict[str, Any]:
    """vcrpy's `filter_headers` covers request headers only; response headers
    (e.g. a `set-cookie` from Cloudflare) need this before-record hook —
    mirroring `spikes/_common.py`, so every cassette keeps secrets REDACTED
    on both sides (enforced by `test_fixture_corpus.py`)."""
    headers = response.get("headers", {})
    for header in list(headers):
        if header.lower() in SECRET_HEADERS:
            headers[header] = ["REDACTED"]
    return response


def make_vcr(*, match_on: list[str] | None = None) -> vcr.VCR:
    extra = {} if match_on is None else {"match_on": match_on}
    return vcr.VCR(
        cassette_library_dir=str(CASSETTES_DIR),
        filter_headers=[(h, "REDACTED") for h in SECRET_HEADERS],
        filter_query_parameters=[(p, "REDACTED") for p in SECRET_QUERY_PARAMS],
        before_record_response=_scrub_response_headers,
        decode_compressed_response=True,
        **extra,
    )


@contextmanager
def replay_cassette(name: str, *, match_on: list[str] | None = None) -> Iterator[None]:
    """Replay-only (`record_mode="none"`): raises if a request isn't already
    in the cassette, proving the test needs no live network."""
    with make_vcr(match_on=match_on).use_cassette(f"{name}.yaml", record_mode="none"):
        yield


__all__ = ["CASSETTES_DIR", "make_vcr", "replay_cassette"]
