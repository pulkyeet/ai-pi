import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

afterEach(() => {
  cleanup();
});

// jsdom has no real layout engine, so `scrollIntoView` (used by
// `SpanHighlight` to bring the highlighted span into view) is missing —
// stub it so component tests don't crash on mount.
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}
