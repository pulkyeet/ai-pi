import type { ConfidenceInputs, Grade } from "./types";

// Mirrors `api.evidence.confidence`'s named constants verbatim, for display
// only — the backend computes the real number; this just re-derives the
// same arithmetic so the drill-down panel can show *why* a score is what it
// is (phase-13-frontend.md: "turns a number into an argument"). Never used
// to compute a confidence value that gets persisted or trusted — only to
// label `ClaimDrilldown.confidence`'s components.
const BASE: Record<Grade, number> = { A: 0.9, B: 0.75, C: 0.55, D: 0.35 };
const DOMAIN_BONUS_PER_STEP = 0.05;
const MAX_DOMAIN_BONUS_STEPS = 4;
const DECAY_PER_30_DAYS = 0.98;
const CONTRADICTION_PENALTY = 0.6;

export interface ConfidenceBreakdown {
  base: number;
  domainMultiplier: number;
  decay: number;
  penalty: number;
  formula: string;
}

export function breakdownConfidence(inputs: ConfidenceInputs): ConfidenceBreakdown {
  const base = BASE[inputs.best_grade];
  const domainMultiplier =
    1 + DOMAIN_BONUS_PER_STEP * Math.min(inputs.n_distinct_domains - 1, MAX_DOMAIN_BONUS_STEPS);
  const decay = DECAY_PER_30_DAYS ** (inputs.age_days / 30);
  const penalty = inputs.contradicted ? CONTRADICTION_PENALTY : 1.0;

  const formula = [
    `${base.toFixed(2)} (grade ${inputs.best_grade})`,
    `${domainMultiplier.toFixed(2)} (${inputs.n_distinct_domains} domain${inputs.n_distinct_domains === 1 ? "" : "s"})`,
    `${decay.toFixed(2)} (${Math.round(inputs.age_days)} days)`,
    `${penalty.toFixed(2)}${inputs.contradicted ? " (contradicted)" : ""}`,
  ].join(" × ");

  return { base, domainMultiplier, decay, penalty, formula };
}
