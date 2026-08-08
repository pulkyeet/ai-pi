// Wire types, kept field-for-field identical to the Pydantic models they
// mirror (src/api/models/report.py, src/api/models/events.py,
// src/api/models/plan.py, src/api/web/routes/*.py) — snake_case preserved
// rather than transformed, so a diff against the backend response is a
// literal diff, not a translation exercise.

export type Grade = "A" | "B" | "C" | "D";

export type Maturity = "established" | "funded" | "indie" | "hobby" | "abandoned";

export interface ResearchBrief {
  category: string;
  segment: string;
  geography: string;
  monetisation_guess: string;
  field_confidence: Record<string, number>;
}

export interface CompetitorPricing {
  model: string;
  entry_usd_month: number;
  free_tier: boolean;
}

export interface CompetitorEntry {
  entity_key: string;
  display_name: string;
  maturity: Maturity | null;
  positioning: string;
  pricing: CompetitorPricing;
  claim_ids: number[];
}

export interface PricingLandscape {
  median_entry_usd_month: number;
  spread: [number, number];
  claim_ids: number[];
}

export interface PainPoint {
  theme: string;
  support_count: number;
  distinct_threads: number;
  grade: Grade;
  confidence: number;
  claim_ids: number[];
}

export interface FeatureGap {
  statement: string;
  addresses_finding_ids: number[];
}

export interface ContradictionValue {
  v: number | string;
  src: number;
  grade: Grade;
  as_of: string;
}

export interface Contradiction {
  entity_key: string;
  attribute: string;
  values: ContradictionValue[];
}

export interface MVP {
  statement: string;
  addresses_finding_ids: number[];
}

export interface Risk {
  statement: string;
  addresses_finding_ids: number[];
}

export interface Coverage {
  score: number;
  failed_branches: string[];
}

export interface Freshness {
  median_source_age_days: number;
  oldest: string;
}

export interface Meta {
  cost_usd: number;
  duration_s: number;
  sources_fetched: number;
  cache_hit_rate: number;
}

export interface Report {
  run_id: string;
  query: string;
  brief: ResearchBrief;
  competitors: CompetitorEntry[];
  pricing_landscape: PricingLandscape;
  pain_points: PainPoint[];
  feature_gaps: FeatureGap[];
  contradictions: Contradiction[];
  mvp: MVP;
  risks: Risk[];
  coverage: Coverage;
  freshness: Freshness;
  meta: Meta;
}

export interface BenchmarkReportSummary {
  run_id: string;
  query: string;
  report: Report;
}

export interface RunAccepted {
  run_id: string;
  status: string;
  disambiguation_fields: string[];
}

export interface RunStatus {
  run_id: string;
  query: string;
  status: string;
  cost_usd: number | null;
  coverage: number | null;
  brief: ResearchBrief | null;
  disambiguation_fields: string[] | null;
  queue_position: number;
}

export interface OtherClaim {
  claim_id: number;
  attribute: string;
  value_text: string | null;
  value_num: number | null;
  quote: string;
}

export interface ConfidenceInputs {
  best_grade: Grade;
  n_distinct_domains: number;
  age_days: number;
  contradicted: boolean;
}

export interface ClaimDrilldown {
  claim_id: number;
  attribute: string;
  value_text: string | null;
  value_num: number | null;
  quote: string;
  char_start: number;
  char_end: number;
  quote_context: string;
  context_offset: number;
  source_url: string;
  source_text: string | null;
  source_fetched_at: string;
  grade: Grade;
  confidence: number;
  confidence_inputs: ConfidenceInputs | null;
  other_claims: OtherClaim[];
}

export interface FindingDrilldown {
  finding_id: number;
  statement: string;
  claim_ids: number[];
}

export interface HealthResponse {
  status: string;
  kill_switch_enabled: boolean;
  kill_switch_reason: string | null;
}

export interface ApiErrorEnvelope {
  error: { code: string; message: string; correlation_id: string };
}

// --- Plan / task DAG (src/api/models/plan.py) ---

export type TaskKind =
  | "discover_competitors"
  | "profile_product"
  | "extract_pricing"
  | "mine_community"
  | "oss_profile"
  | "find_funding"
  | "trend_signals";

export interface PlanNode {
  id: string;
  kind: TaskKind;
  args: Record<string, unknown>;
  budget_weight: number;
}

export interface Plan {
  nodes: PlanNode[];
  edges: [string, string][];
  total_budget_weight: number;
}

// --- SSE events (src/api/models/events.py / src/api/web/sse.py) ---

export interface PlanCreatedEvent {
  type: "plan.created";
  run_id: string;
  plan: Plan;
}

export interface TaskStartedEvent {
  type: "task.started";
  run_id: string;
  task_id: number;
  kind: TaskKind;
}

export interface TaskCompletedEvent {
  type: "task.completed";
  run_id: string;
  task_id: number;
  kind: TaskKind;
  cost_usd: number | null;
  latency_ms: number | null;
}

export interface TaskFailedEvent {
  type: "task.failed";
  run_id: string;
  task_id: number;
  kind: TaskKind;
  error: string;
}

export interface FindingAddedEvent {
  type: "finding.added";
  run_id: string;
  finding_id: number;
  kind: string;
  statement: string;
}

export interface ReportReadyEvent {
  type: "report.ready";
  run_id: string;
}

export type RunEvent =
  | PlanCreatedEvent
  | TaskStartedEvent
  | TaskCompletedEvent
  | TaskFailedEvent
  | FindingAddedEvent
  | ReportReadyEvent;

export interface DisambiguationOverrides {
  category?: string;
  segment?: string;
  geography?: string;
  monetisation_guess?: string;
}
