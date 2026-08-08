// Python string indices are code points; JavaScript string indices are
// UTF-16 code units. `[char_start, char_end)` on a `ClaimDrilldown` is
// always a Python code-point offset (src/api/extract/span.py's `bind_span`),
// so it must be converted before it can index into a JS string — silently
// skipping this is the phase's named primary risk (phase-13-frontend.md:
// "The trap: Python and JavaScript disagree about string indices").
export function cpToUtf16(text: string, cpIndex: number): number {
  return [...text].slice(0, cpIndex).join("").length;
}

export interface HighlightRange {
  start: number;
  end: number;
}

// Converts a Python code-point `[char_start, char_end)` span into a UTF-16
// `[start, end)` range safe to slice a JS string with.
export function spanToUtf16Range(text: string, charStart: number, charEnd: number): HighlightRange {
  return { start: cpToUtf16(text, charStart), end: cpToUtf16(text, charEnd) };
}

export interface HighlightedSegments {
  before: string;
  highlighted: string;
  after: string;
}

export function splitAtSpan(text: string, charStart: number, charEnd: number): HighlightedSegments {
  const { start, end } = spanToUtf16Range(text, charStart, charEnd);
  return {
    before: text.slice(0, start),
    highlighted: text.slice(start, end),
    after: text.slice(end),
  };
}

export interface DrilldownSpanSource {
  source_text: string | null;
  quote_context: string;
  context_offset: number;
  quote: string;
  char_start: number;
  char_end: number;
}

export interface ResolvedHighlightSpan {
  text: string;
  charStart: number;
  charEnd: number;
}

// Picks which text to highlight against: the full page text with its
// original span when still cached, or the saved `quote_context` window at
// `context_offset` once `sources.extracted_text` has been evicted (Phase
// 03's TTL) — `quote_context_window`'s own `context_offset` is exactly
// "the offset of `span.start` within that window" (src/api/extract/
// span.py), so the highlight length is the quote's own code-point count,
// not the original span's width.
export function resolveHighlightSpan(drilldown: DrilldownSpanSource): ResolvedHighlightSpan {
  if (drilldown.source_text !== null) {
    return { text: drilldown.source_text, charStart: drilldown.char_start, charEnd: drilldown.char_end };
  }
  const quoteCodePointLength = [...drilldown.quote].length;
  return {
    text: drilldown.quote_context,
    charStart: drilldown.context_offset,
    charEnd: drilldown.context_offset + quoteCodePointLength,
  };
}
