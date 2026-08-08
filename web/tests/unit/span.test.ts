import { describe, expect, it } from "vitest";
import fc from "fast-check";
import { cpToUtf16, resolveHighlightSpan, splitAtSpan } from "@/lib/span";

// An independent reference implementation (manual UTF-16 surrogate-pair
// walk, not the spread-operator technique `cpToUtf16` itself uses) so the
// property test below is a genuine cross-check, not the implementation
// testing itself. Mirrors what Python's `str` indexing does semantically:
// each code point — including astral ones represented as a surrogate pair
// in UTF-16 — counts as exactly one Python index step.
function referenceCpToUtf16(text: string, cpIndex: number): number {
  let utf16 = 0;
  let cp = 0;
  while (cp < cpIndex && utf16 < text.length) {
    const code = text.charCodeAt(utf16);
    const isHighSurrogate = code >= 0xd800 && code <= 0xdbff;
    const nextIsLowSurrogate =
      isHighSurrogate &&
      utf16 + 1 < text.length &&
      text.charCodeAt(utf16 + 1) >= 0xdc00 &&
      text.charCodeAt(utf16 + 1) <= 0xdfff;
    utf16 += nextIsLowSurrogate ? 2 : 1;
    cp += 1;
  }
  return utf16;
}

describe("cpToUtf16", () => {
  it("is a no-op for pure ASCII", () => {
    const text = "Starts at $29/mo";
    expect(cpToUtf16(text, 0)).toBe(0);
    expect(cpToUtf16(text, 11)).toBe(11);
    expect(cpToUtf16(text, text.length)).toBe(text.length);
  });

  it("converts correctly across an emoji (astral, 2 UTF-16 units, 1 code point)", () => {
    // code points: 🎉(1) ' '(1) '$'(1) '2'(1) '9'(1) '/'(1) 'm'(1) 'o'(1)
    const text = "🎉 $29/mo";
    const dollarCodePointIndex = 2; // after 🎉 and the space, both single code points
    expect(cpToUtf16(text, dollarCodePointIndex)).toBe(3); // 🎉 takes 2 UTF-16 units + the space
    expect(text.slice(cpToUtf16(text, dollarCodePointIndex))).toBe("$29/mo");
  });

  it("converts correctly across an astral CJK Extension B character", () => {
    // U+20000 (CJK Extension B) is a surrogate pair in UTF-16 but one Python code point.
    const text = "\u{20000} Price: $5";
    const dollarCodePointIndex = [..."\u{20000} Price: "].length;
    expect(cpToUtf16(text, dollarCodePointIndex)).toBe("\u{20000} Price: ".length);
    expect(text.slice(cpToUtf16(text, dollarCodePointIndex))).toBe("$5");
  });

  it("converts correctly across mathematical alphanumeric symbols (astral)", () => {
    // U+1D400 MATHEMATICAL BOLD CAPITAL A, U+1D401 ...B — each a surrogate pair.
    const text = "\u{1D400}\u{1D401} costs $10";
    const dollarCodePointIndex = [..."\u{1D400}\u{1D401} costs "].length;
    expect(cpToUtf16(text, dollarCodePointIndex)).toBe("\u{1D400}\u{1D401} costs ".length);
    expect(text.slice(cpToUtf16(text, dollarCodePointIndex))).toBe("$10");
  });

  it("matches an independently-implemented UTF-16 walk over generated Unicode strings", () => {
    fc.assert(
      fc.property(
        fc.string({ unit: "grapheme-composite", minLength: 0, maxLength: 40 }),
        fc.nat(60),
        (text, rawIndex) => {
          const codePointLength = [...text].length;
          const cpIndex = Math.min(rawIndex, codePointLength);
          expect(cpToUtf16(text, cpIndex)).toBe(referenceCpToUtf16(text, cpIndex));
        },
      ),
      { numRuns: 500 },
    );
  });
});

describe("splitAtSpan", () => {
  it("highlights a span at the very start of the text", () => {
    const { before, highlighted, after } = splitAtSpan("Starts at $29/mo", 0, 6);
    expect(before).toBe("");
    expect(highlighted).toBe("Starts");
    expect(after).toBe(" at $29/mo");
  });

  it("highlights a span at the very end of the text", () => {
    const text = "Starts at $29/mo";
    const { before, highlighted, after } = splitAtSpan(text, 10, text.length);
    expect(before).toBe("Starts at ");
    expect(highlighted).toBe("$29/mo");
    expect(after).toBe("");
  });

  it("highlights a span spanning the whole text", () => {
    const text = "Starts at $29/mo";
    const { before, highlighted, after } = splitAtSpan(text, 0, text.length);
    expect(before).toBe("");
    expect(highlighted).toBe(text);
    expect(after).toBe("");
  });

  it("highlights correctly when the text contains an emoji before the span", () => {
    const text = "🎉 Starts at $29/mo";
    const start = [..."🎉 Starts at "].length;
    const { highlighted } = splitAtSpan(text, start, start + [..."$29/mo"].length);
    expect(highlighted).toBe("$29/mo");
  });
});

describe("resolveHighlightSpan", () => {
  const base = {
    quote: "$29/mo",
    quote_context: "Our pricing: $29/mo billed annually",
    context_offset: 13,
    char_start: 40,
    char_end: 46,
  };

  it("uses source_text and the original span when source text is still cached", () => {
    const resolved = resolveHighlightSpan({ ...base, source_text: "...padding... $29/mo ...padding..." });
    expect(resolved).toEqual({
      text: "...padding... $29/mo ...padding...",
      charStart: 40,
      charEnd: 46,
    });
  });

  it("falls back to quote_context + context_offset when source_text has been evicted", () => {
    const resolved = resolveHighlightSpan({ ...base, source_text: null });
    expect(resolved.text).toBe(base.quote_context);
    expect(resolved.charStart).toBe(13);
    expect(resolved.text.slice(resolved.charStart, resolved.charEnd)).toBe("$29/mo");
  });
});
