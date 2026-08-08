// Pure fixture data, no Playwright imports — shared by `fixtures.ts` (page.
// route mocking, used from Playwright test processes) and `mock-server.ts`
// (a plain Node http server, used to answer the Next.js *build-time*
// server-side fetch that renders the static homepage — see mock-server.ts's
// own comment for why that fetch can't be reached by `page.route` at all).
import type { BenchmarkReportSummary, ClaimDrilldown, FindingDrilldown, Report } from "../../lib/types";

export const RUN_ID = "r_bench_001";

const SOURCE_TEXT =
  "Acme is a leading expense tool for freelancers. Starts at $29/mo today. Trusted by many teams.";
const QUOTE = "$29/mo today";
const CHAR_START = SOURCE_TEXT.indexOf(QUOTE);
const CHAR_END = CHAR_START + QUOTE.length;

const PAIN_SOURCE_TEXT = "Receipts pile up every month and categorising them by hand is tedious.";
const PAIN_QUOTE = "categorising them by hand is tedious";
const PAIN_CHAR_START = PAIN_SOURCE_TEXT.indexOf(PAIN_QUOTE);
const PAIN_CHAR_END = PAIN_CHAR_START + PAIN_QUOTE.length;

export const REPORT: Report = {
  run_id: RUN_ID,
  query: "AI expense tracker for freelancers",
  brief: {
    category: "expense management",
    segment: "B2B, freelancers and micro SMB",
    geography: "global",
    monetisation_guess: "seat based SaaS",
    field_confidence: { segment: 0.55, geography: 0.4 },
  },
  competitors: [
    {
      entity_key: "web:acme-expense.com",
      display_name: "Acme Expense",
      maturity: "established",
      positioning: "Starts at $29/mo today, aimed at small teams",
      pricing: { model: "seat", entry_usd_month: 29, free_tier: true },
      claim_ids: [101],
    },
  ],
  pricing_landscape: { median_entry_usd_month: 12, spread: [0, 49], claim_ids: [101] },
  pain_points: [
    {
      theme: "manual receipt categorisation",
      support_count: 23,
      distinct_threads: 7,
      grade: "D",
      confidence: 0.61,
      claim_ids: [102],
    },
  ],
  feature_gaps: [{ statement: "No bank-feed auto-categorisation", addresses_finding_ids: [201] }],
  contradictions: [
    {
      entity_key: "web:acme-expense.com",
      attribute: "pricing.entry_usd_month",
      values: [
        { v: 5, src: 88, grade: "A", as_of: "2026-07-30" },
        { v: 18, src: 142, grade: "C", as_of: "2025-11-02" },
      ],
    },
  ],
  mvp: { statement: "Ship a seat-based tier with auto-categorisation first", addresses_finding_ids: [201] },
  risks: [{ statement: "Category incumbents may add freelancer tiers", addresses_finding_ids: [202] }],
  coverage: { score: 0.82, failed_branches: ["funding"] },
  freshness: { median_source_age_days: 41, oldest: "2025-03-11" },
  meta: { cost_usd: 0.031, duration_s: 128, sources_fetched: 64, cache_hit_rate: 0.31 },
};

export const BENCHMARK_LIST: BenchmarkReportSummary[] = [
  { run_id: RUN_ID, query: REPORT.query, report: REPORT },
];

export const CLAIMS: Record<number, ClaimDrilldown> = {
  101: {
    claim_id: 101,
    attribute: "pricing.entry_usd_month",
    value_text: null,
    value_num: 29,
    quote: QUOTE,
    char_start: CHAR_START,
    char_end: CHAR_END,
    quote_context: SOURCE_TEXT,
    context_offset: CHAR_START,
    source_url: "https://acme-expense.com/pricing",
    source_text: SOURCE_TEXT,
    source_fetched_at: "2026-07-30T00:00:00Z",
    grade: "A",
    confidence: 0.9,
    confidence_inputs: { best_grade: "A", n_distinct_domains: 2, age_days: 5, contradicted: false },
    other_claims: [
      {
        claim_id: 103,
        attribute: "pricing.free_tier",
        value_text: "true",
        value_num: null,
        quote: "free tier available",
      },
    ],
  },
  102: {
    claim_id: 102,
    attribute: "complaint.receipts",
    value_text: PAIN_QUOTE,
    value_num: null,
    quote: PAIN_QUOTE,
    char_start: PAIN_CHAR_START,
    char_end: PAIN_CHAR_END,
    quote_context: PAIN_SOURCE_TEXT,
    context_offset: PAIN_CHAR_START,
    source_url: "https://news.ycombinator.com/item?id=1",
    // Simulates Phase 03's TTL eviction: no cached full page text, so the
    // panel must fall back to quote_context + context_offset.
    source_text: null,
    source_fetched_at: "2026-06-01T00:00:00Z",
    grade: "D",
    confidence: 0.35,
    confidence_inputs: { best_grade: "D", n_distinct_domains: 1, age_days: 68, contradicted: false },
    other_claims: [],
  },
};

const FREE_TIER_SOURCE_TEXT = "A free tier available for solo users.";
const FREE_TIER_QUOTE = "free tier available";
const FREE_TIER_START = FREE_TIER_SOURCE_TEXT.indexOf(FREE_TIER_QUOTE);
const FREE_TIER_END = FREE_TIER_START + FREE_TIER_QUOTE.length;

CLAIMS[103] = {
  claim_id: 103,
  attribute: "pricing.free_tier",
  value_text: "true",
  value_num: null,
  quote: FREE_TIER_QUOTE,
  char_start: FREE_TIER_START,
  char_end: FREE_TIER_END,
  quote_context: FREE_TIER_SOURCE_TEXT,
  context_offset: FREE_TIER_START,
  source_url: CLAIMS[101]!.source_url,
  source_text: FREE_TIER_SOURCE_TEXT,
  source_fetched_at: "2026-07-30T00:00:00Z",
  grade: "A",
  confidence: 0.85,
  confidence_inputs: { best_grade: "A", n_distinct_domains: 1, age_days: 5, contradicted: false },
  other_claims: [
    { claim_id: 101, attribute: "pricing.entry_usd_month", value_text: null, value_num: 29, quote: QUOTE },
  ],
};

export const FINDINGS: Record<number, FindingDrilldown> = {
  201: { finding_id: 201, statement: REPORT.mvp.statement, claim_ids: [101] },
  202: { finding_id: 202, statement: REPORT.risks[0]!.statement, claim_ids: [102] },
};
