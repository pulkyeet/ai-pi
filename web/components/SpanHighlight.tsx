"use client";

import { useEffect, useRef } from "react";
import { splitAtSpan } from "@/lib/span";

export interface SpanHighlightProps {
  text: string;
  charStart: number;
  charEnd: number;
  scrollIntoView?: boolean;
}

// Renders `text` with `[charStart, charEnd)` — a Python code-point span —
// highlighted. This is the critical component named in the phase doc: the
// whole product claim ("every sentence binds to an exact span") is only as
// real as this component's offset arithmetic.
export function SpanHighlight({ text, charStart, charEnd, scrollIntoView = true }: SpanHighlightProps) {
  const markRef = useRef<HTMLElement>(null);
  const { before, highlighted, after } = splitAtSpan(text, charStart, charEnd);

  useEffect(() => {
    if (scrollIntoView) {
      markRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [scrollIntoView, charStart, charEnd, text]);

  return (
    <p style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}>
      {before}
      <mark
        ref={markRef}
        data-testid="span-highlight"
        style={{ background: "var(--highlight)", color: "inherit", borderRadius: 2, padding: "0 1px" }}
      >
        {highlighted}
      </mark>
      {after}
    </p>
  );
}
