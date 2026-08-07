"""Span binding — the single mechanism the entire product promise rests on
(masterplan §4.8, phase doc). Models fabricate character offsets confidently,
so offsets are never taken from the model: the model is asked for the exact
verbatim quote, and the span is located locally against the page's stored
text.

Three deliberate choices, all load-bearing:

**No fuzzy matching, ever.** Not `difflib`, not normalised comparison, not
whitespace-insensitive matching. The moment a near-match is accepted, the
guarantee degrades from "this text is on that page" to "something like this
text is roughly on that page".

**Ambiguous quotes are dropped.** If the quote appears more than once, there
is no correct span. Picking the first occurrence would produce a citation
that highlights the wrong instance — worse than a missing claim.

**No normalisation at bind time.** `api.retrieval.extract_text` owns
normalisation and applies it exactly once, at write. Re-normalising here
would shift offsets relative to stored text and break every span.
"""

from __future__ import annotations

from dataclasses import dataclass

CONTEXT_RADIUS = 2000


@dataclass(frozen=True)
class Span:
    start: int
    end: int


def bind_span(source_text: str, quote: str) -> Span | None:
    """masterplan §4.8, verbatim. `source_text.find` is a Python string
    (code point) index, not a byte offset — Phase 13's frontend must use the
    same unit, since JS strings are UTF-16 and surrogate pairs differ."""
    if not quote:
        return None
    idx = source_text.find(quote)
    if idx == -1:
        return None
    if source_text.find(quote, idx + 1) != -1:
        return None
    return Span(start=idx, end=idx + len(quote))


def quote_context_window(
    source_text: str, span: Span, *, radius: int = CONTEXT_RADIUS
) -> tuple[str, int]:
    """±`radius` chars around `span`, clamped to the text's bounds, plus the
    offset of `span.start` within that window. This is what lets drill-down
    survive Phase 03's TTL eviction of `sources.extracted_text` — the UI
    highlights `quote` at `context_offset` within `quote_context` once full
    source text is gone (Phase 00's `claims.quote_context`/`context_offset`
    columns)."""
    ctx_start = max(0, span.start - radius)
    ctx_end = min(len(source_text), span.end + radius)
    return source_text[ctx_start:ctx_end], span.start - ctx_start


__all__ = ["CONTEXT_RADIUS", "Span", "bind_span", "quote_context_window"]
