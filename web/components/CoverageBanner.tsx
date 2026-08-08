import type { Coverage } from "@/lib/types";

export interface CoverageBannerProps {
  coverage: Coverage;
}

// Masterplan Rule 4: "a run whose funding branch died says so, out loud, on
// the report." Hidden at 100% coverage (nothing to disclose); otherwise
// sits above the report, not tucked into a footer.
export function CoverageBanner({ coverage }: CoverageBannerProps) {
  if (coverage.score >= 1 && coverage.failed_branches.length === 0) return null;

  const pct = Math.round(coverage.score * 100);
  return (
    <div
      role="status"
      data-testid="coverage-banner"
      style={{
        display: "flex",
        gap: 8,
        alignItems: "baseline",
        flexWrap: "wrap",
        padding: "10px 14px",
        borderRadius: 8,
        background: "var(--amber-bg)",
        color: "var(--amber)",
        border: "1px solid var(--amber)",
        fontSize: 14,
      }}
    >
      <strong>Coverage {pct}%</strong>
      {coverage.failed_branches.length > 0 && (
        <span>
          — {coverage.failed_branches.join(", ")} data unavailable for this category
        </span>
      )}
    </div>
  );
}
