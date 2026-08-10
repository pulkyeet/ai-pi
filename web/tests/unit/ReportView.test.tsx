import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReportView } from "@/components/ReportView";
import type { Report } from "@/lib/types";

function reportWithModel(model: string, entryUsdMonth: number): Report {
  return {
    run_id: "r_test_reportview",
    query: "static site generators",
    brief: {
      category: "static site generators",
      segment: "",
      geography: "",
      monetisation_guess: "",
      field_confidence: {},
    },
    competitors: [
      {
        entity_key: "web:docusaurus.io",
        display_name: "Docusaurus",
        maturity: null,
        positioning: "Tier competitor",
        pricing: { model, entry_usd_month: entryUsdMonth, free_tier: false },
        claim_ids: [1],
      },
    ],
    pricing_landscape: {
      median_entry_usd_month: 0,
      spread: [0, 0],
      claim_ids: [1],
    },
    pain_points: [],
    feature_gaps: [],
    contradictions: [],
    mvp: { statement: "", addresses_finding_ids: [] },
    risks: [],
    coverage: { score: 0.75, failed_branches: [] },
    freshness: { median_source_age_days: 10, oldest: "2026-07-01" },
    meta: { cost_usd: 0.01, duration_s: 10, sources_fetched: 3, cache_hit_rate: 0.5 },
  };
}

describe("ReportView", () => {
  it("renders a permanently-free competitor as 'Free' without a price", () => {
    render(<ReportView report={reportWithModel("free", 0)} />);
    expect(screen.getByText("Free")).toBeInTheDocument();
    expect(screen.queryByText("$/mo · free")).not.toBeInTheDocument();
  });

  it("renders a paid competitor with entry price and model", () => {
    render(<ReportView report={reportWithModel("seat", 29)} />);
    expect(screen.getByText("$29/mo · seat")).toBeInTheDocument();
  });
});
