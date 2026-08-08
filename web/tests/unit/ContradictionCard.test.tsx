import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ContradictionCard } from "@/components/ContradictionCard";
import type { Contradiction } from "@/lib/types";

describe("ContradictionCard", () => {
  const contradiction: Contradiction = {
    entity_key: "web:expensify.com",
    attribute: "pricing.entry_usd_month",
    values: [
      { v: 18, src: 142, grade: "C", as_of: "2025-11-02" },
      { v: 5, src: 88, grade: "A", as_of: "2026-07-30" },
    ],
  };

  it("shows both values with their own grades and dates", () => {
    render(<ContradictionCard contradiction={contradiction} />);
    const card = screen.getByTestId("contradiction-card");
    expect(card).toHaveTextContent("18");
    expect(card).toHaveTextContent("grade C");
    expect(card).toHaveTextContent("2025-11-02");
    expect(card).toHaveTextContent("5");
    expect(card).toHaveTextContent("grade A");
    expect(card).toHaveTextContent("2026-07-30");
  });

  it("marks the highest-grade value as the one shown in the report", () => {
    render(<ContradictionCard contradiction={contradiction} />);
    expect(screen.getByText("shown in report")).toBeInTheDocument();
    // Only the grade-A ($5) row should carry the winner marker.
    const rows = screen.getAllByText(/^(18|5)$/);
    expect(rows).toHaveLength(2);
  });

  it("breaks a grade tie on the most recent as_of", () => {
    const tied: Contradiction = {
      entity_key: "web:acme.com",
      attribute: "pricing.entry_usd_month",
      values: [
        { v: 9, src: 1, grade: "A", as_of: "2025-01-01" },
        { v: 12, src: 2, grade: "A", as_of: "2026-01-01" },
      ],
    };
    render(<ContradictionCard contradiction={tied} />);
    const winnerRow = screen.getByText("shown in report").closest("div");
    expect(winnerRow).toHaveTextContent("12");
  });
});
