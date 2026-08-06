"""Phase 01 exit criteria: the committed cassette corpus never leaks a
credential, and every cassette replays offline without hitting the network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import vcr
import yaml

CASSETTES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "cassettes"
CASSETTE_FILES = sorted(CASSETTES_DIR.glob("*.yaml"))

_KEY_SHAPED = re.compile(
    r"bearer\s+[a-z0-9._-]{16,}"
    r"|sk-[a-z0-9]{16,}"
    r"|\bkey-[a-z0-9]{16,}\b",
    re.IGNORECASE,
)
_REDACTED_HEADERS = {"authorization", "x-api-key", "x-apikey", "api-key", "cookie", "set-cookie"}


@pytest.mark.parametrize("cassette_path", CASSETTE_FILES, ids=lambda p: p.name)
def test_cassette_has_no_key_shaped_secret(cassette_path: Path) -> None:
    text = cassette_path.read_text(encoding="utf-8")
    assert not _KEY_SHAPED.search(text), f"{cassette_path.name} contains a credential-shaped string"


@pytest.mark.parametrize("cassette_path", CASSETTE_FILES, ids=lambda p: p.name)
def test_cassette_secret_headers_are_redacted(cassette_path: Path) -> None:
    data = yaml.safe_load(cassette_path.read_text(encoding="utf-8"))
    for interaction in data.get("interactions", []):
        for side in ("request", "response"):
            headers = interaction[side].get("headers", {}) or {}
            for name, values in headers.items():
                if name.lower() in _REDACTED_HEADERS:
                    assert values == ["REDACTED"], (
                        f"{cassette_path.name}: {side} header {name!r} not redacted: {values!r}"
                    )


@pytest.mark.parametrize("cassette_path", CASSETTE_FILES, ids=lambda p: p.name)
def test_cassette_replays_offline(cassette_path: Path) -> None:
    """None record mode raises if a request isn't already in the cassette —
    proving later phases can replay this fixture without any live network."""
    import httpx

    my_vcr = vcr.VCR(cassette_library_dir=str(CASSETTES_DIR))
    with my_vcr.use_cassette(cassette_path.name, record_mode="none"):
        data = yaml.safe_load(cassette_path.read_text(encoding="utf-8"))
        interactions = data.get("interactions", [])
        assert interactions, f"{cassette_path.name} has no recorded interactions"
        first = interactions[0]["request"]
        with httpx.Client(timeout=5) as client:
            resp = client.request(first["method"], first["uri"])
            assert resp.status_code > 0
