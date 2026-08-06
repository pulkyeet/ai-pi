"""Extraction-quality corpus: real HTML pricing pages, reusing the Phase 01
fixture corpus (`tests/fixtures/pages/`) rather than committing a second one.

`manifest.json`'s `matched_price_snippet` was captured by the Phase 01 spike
matching against *raw* HTML OR extracted text (see `spikes/crawl_static.py`),
so it isn't guaranteed to survive into this layer's `favor_precision=True`
extraction verbatim — asserting an exact substring here would be testing the
wrong invariant. What this layer actually needs is: does the *extracted,
normalised* text still read as price-shaped to `pathguess.looks_price_shaped`,
the same routing heuristic Phase 03 itself uses. That is what's asserted,
both per-page for a handful of clean marketing pages and in aggregate against
the Phase 01 baseline (88% static hit rate) for the full corpus.
"""

from __future__ import annotations

import json
from pathlib import Path

from api.retrieval.extract_text import extract, normalise
from api.retrieval.pathguess import looks_price_shaped

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "pages"
MANIFEST = json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))

_CASES = [
    entry["domain"]
    for entry in MANIFEST
    if entry.get("fetched") and entry.get("has_price") and entry.get("matched_price_snippet")
]

# A handful of straightforward, non-enterprise marketing pages that should
# reliably extract as price-shaped — a fast, specific canary distinct from
# the aggregate corpus check below.
_RELIABLE_PAGES = ["asana.com", "trello.com", "monday.com", "notion.so", "slack.com"]


def _page_html(domain: str) -> str:
    return (FIXTURES_DIR / (domain.replace(".", "_") + ".html")).read_text(
        encoding="utf-8", errors="replace"
    )


def test_corpus_is_non_trivial() -> None:
    assert len(_CASES) >= 15


def test_reliable_pages_extract_as_price_shaped() -> None:
    for domain in _RELIABLE_PAGES:
        assert domain in _CASES, domain
        text = extract(_page_html(domain), url=f"https://{domain}/pricing")
        assert text is not None, f"{domain}: trafilatura returned no text"
        assert looks_price_shaped(text), f"{domain}: extracted text lost its price shape"


def test_extraction_output_is_already_normalised() -> None:
    domain = _RELIABLE_PAGES[0]
    text = extract(_page_html(domain), url=f"https://{domain}/pricing")
    assert text is not None
    assert normalise(text) == text


def test_corpus_price_shaped_hit_rate_meets_phase01_baseline() -> None:
    hits = 0
    for domain in _CASES:
        text = extract(_page_html(domain), url=f"https://{domain}/pricing")
        if text is not None and looks_price_shaped(text):
            hits += 1
    rate = hits / len(_CASES)
    # Phase 01 measured 88% (35/40) with default trafilatura settings; this
    # layer's favor_precision=True trades a little recall for cleaner output
    # (masterplan §4.8's byte-identical-text guarantee cares more about
    # precision than recall). 80% keeps this a real regression guard without
    # being flaky on the known enterprise-vendor misses (Atlassian, Salesforce
    # — see spikes/pricing_corpus.py).
    assert rate >= 0.80, f"price-shaped extraction hit rate {rate:.0%} ({hits}/{len(_CASES)})"
