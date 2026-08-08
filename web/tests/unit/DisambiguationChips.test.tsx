import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DisambiguationChips } from "@/components/DisambiguationChips";
import type { ResearchBrief } from "@/lib/types";

const brief: ResearchBrief = {
  category: "expense management",
  segment: "B2B",
  geography: "global",
  monetisation_guess: "seat based SaaS",
  field_confidence: { segment: 0.55, geography: 0.4 },
};

describe("DisambiguationChips", () => {
  it("shows at most two chips with the best guess pre-selected", () => {
    const onChange = vi.fn();
    render(<DisambiguationChips brief={brief} fields={["segment", "geography"]} onChange={onChange} />);
    expect(screen.getByText(/B2B/)).toBeInTheDocument();
    expect(screen.getByText(/global/)).toBeInTheDocument();
  });

  it("never calls onChange until the visitor actually edits a chip (ignorable by default)", async () => {
    const onChange = vi.fn();
    render(<DisambiguationChips brief={brief} fields={["segment"]} onChange={onChange} />);
    expect(onChange).not.toHaveBeenCalled();

    await userEvent.click(screen.getByText(/B2B/));
    const input = screen.getByLabelText("Edit Segment");
    await userEvent.clear(input);
    await userEvent.type(input, "B2C{Enter}");

    expect(onChange).toHaveBeenCalledWith({ segment: "B2C" });
  });

  it("renders nothing when there are no ambiguous fields", () => {
    const { container } = render(<DisambiguationChips brief={brief} fields={[]} onChange={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
