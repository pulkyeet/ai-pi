import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CoverageBanner } from "@/components/CoverageBanner";

describe("CoverageBanner", () => {
  it("renders failed branches with the coverage percentage", () => {
    render(<CoverageBanner coverage={{ score: 0.82, failed_branches: ["funding"] }} />);
    const banner = screen.getByTestId("coverage-banner");
    expect(banner).toHaveTextContent("82%");
    expect(banner).toHaveTextContent("funding");
  });

  it("renders nothing at 100% coverage with no failed branches", () => {
    render(<CoverageBanner coverage={{ score: 1, failed_branches: [] }} />);
    expect(screen.queryByTestId("coverage-banner")).not.toBeInTheDocument();
  });
});
