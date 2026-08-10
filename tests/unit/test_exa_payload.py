"""Pure payload-construction tests for `ExaProvider` — no network. The
default-mode shape here is what `tests/integration/test_exa_provider.py`
relies on matching a real recorded cassette interaction byte-for-byte."""

from __future__ import annotations

from api.search.exa import _build_payload


def test_default_shape_matches_recorded_cassette_interaction() -> None:
    payload = _build_payload(
        "project management tool", limit=10, site=None, mode="neural", include_contents=True
    )
    assert payload == {
        "query": "project management tool",
        "numResults": 10,
        "type": "neural",
        "contents": {"text": True},
    }
    # key order matters: it's what makes the JSON body match the cassette's
    # recorded bytes when `body` is part of the VCR match criteria.
    assert list(payload.keys()) == ["query", "numResults", "type", "contents"]


def test_bare_eval_set_shape_omits_mode_and_contents() -> None:
    payload = _build_payload("CRM software", limit=10, site=None, mode="", include_contents=False)
    assert payload == {"query": "CRM software", "numResults": 10}


def test_auto_mode_requests_auto_search_type() -> None:
    """Phase 14 follow-up: discovery runs with mode='auto' so Exa picks
    keyword vs neural per query — same flat $0.007/query, but household names
    (which rarely use verbatim category keywords) get a chance to surface."""
    payload = _build_payload(
        "expense tracker", limit=20, site=None, mode="auto", include_contents=True
    )
    assert payload["type"] == "auto"
    assert payload["numResults"] == 20


def test_site_adds_include_domains() -> None:
    payload = _build_payload("acme", limit=5, site="g2.com", mode="", include_contents=False)
    assert payload["includeDomains"] == ["g2.com"]


def test_no_site_omits_include_domains() -> None:
    payload = _build_payload("acme", limit=5, site=None, mode="", include_contents=False)
    assert "includeDomains" not in payload
