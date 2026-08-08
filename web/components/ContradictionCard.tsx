import type { Contradiction, ContradictionValue, Grade } from "@/lib/types";

const GRADE_RANK: Record<Grade, number> = { A: 0, B: 1, C: 2, D: 3 };

// Mirrors `api.evidence.contradictions._pick_winner` (masterplan §4.7):
// highest grade wins, ties broken by most recent `as_of`. The report
// payload carries every value with its own grade/date but not a "winner"
// flag, so the same rule is re-derived here rather than invented — the
// loser is retained and shown, not hidden, per the phase doc ("this is a
// feature, not an apology").
function pickWinner(values: ContradictionValue[]): ContradictionValue {
  return values.reduce((best, candidate) => {
    const gradeDelta = GRADE_RANK[candidate.grade] - GRADE_RANK[best.grade];
    if (gradeDelta < 0) return candidate;
    if (gradeDelta > 0) return best;
    return candidate.as_of > best.as_of ? candidate : best;
  });
}

export interface ContradictionCardProps {
  contradiction: Contradiction;
}

export function ContradictionCard({ contradiction }: ContradictionCardProps) {
  const winner = pickWinner(contradiction.values);
  return (
    <div
      data-testid="contradiction-card"
      style={{
        border: "1px solid var(--border)",
        borderRadius: 10,
        padding: 14,
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      <div style={{ fontSize: 13, color: "var(--fg-muted)" }}>
        {contradiction.entity_key} · <code>{contradiction.attribute}</code>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {contradiction.values.map((value) => {
          const isWinner = value === winner;
          // De-emphasis for the losing value is conveyed structurally (no
          // "shown in report" badge, a plain-weight number) rather than via
          // CSS `opacity` — opacity on text blends it toward the
          // background and silently fails WCAG contrast (axe caught this:
          // 0.65 opacity dropped the muted date text below 4.5:1).
          return (
            <div
              key={`${value.src}-${String(value.v)}`}
              style={{ display: "flex", gap: 10, alignItems: "baseline", fontSize: 14 }}
            >
              <strong style={{ fontWeight: isWinner ? 700 : 400 }}>{String(value.v)}</strong>
              <span
                data-testid="grade-badge"
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  padding: "1px 6px",
                  borderRadius: 999,
                  border: "1px solid var(--border)",
                }}
              >
                grade {value.grade}
              </span>
              <span style={{ color: "var(--fg-muted)" }}>as of {value.as_of}</span>
              {isWinner && (
                <span style={{ color: "var(--green)", fontWeight: 600 }}>shown in report</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
