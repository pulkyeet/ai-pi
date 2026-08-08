import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SpanHighlight } from "@/components/SpanHighlight";

describe("SpanHighlight", () => {
  it("renders the correct range for ASCII text", () => {
    const text = "Starts at $29/mo";
    render(<SpanHighlight text={text} charStart={10} charEnd={text.length} scrollIntoView={false} />);
    expect(screen.getByTestId("span-highlight")).toHaveTextContent("$29/mo");
  });

  it("renders the correct range across an emoji prefix (astral, UTF-16 divergence)", () => {
    // Python code-point offsets: 🎉(0) ' '(1) '$'(2) ... — offset 2 is where
    // "$29/mo" starts in code points, but at UTF-16 offset 3 in JS.
    render(<SpanHighlight text="🎉 $29/mo" charStart={2} charEnd={8} scrollIntoView={false} />);
    expect(screen.getByTestId("span-highlight")).toHaveTextContent("$29/mo");
  });

  it("renders the correct range across an astral CJK Extension B character", () => {
    const text = "\u{20000} Price: $5";
    const start = [..."\u{20000} Price: "].length;
    const end = start + [..."$5"].length;
    render(<SpanHighlight text={text} charStart={start} charEnd={end} scrollIntoView={false} />);
    expect(screen.getByTestId("span-highlight")).toHaveTextContent("$5");
  });

  it("renders the correct range across mathematical alphanumeric symbols", () => {
    const text = "\u{1D400}\u{1D401} costs $10";
    const start = [..."\u{1D400}\u{1D401} costs "].length;
    const end = start + [..."$10"].length;
    render(<SpanHighlight text={text} charStart={start} charEnd={end} scrollIntoView={false} />);
    expect(screen.getByTestId("span-highlight")).toHaveTextContent("$10");
  });
});
