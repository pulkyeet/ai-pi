# Execution Tracker

Last Updated: 2026-08-06

## Current Status

- **Phase**: Pre-implementation — planning complete
- **Focus**: Execution phases derived from the masterplan; ready to start Phase 00
- **Blockers**: None. Two credential requests should start immediately (see Next Steps).

## Recent Activities

### 2026-08-06
- Created `/docs` directory, `tracker.md`, `working_knowledge.md`
- Derived 16 execution phases from `ai-product-investigator-masterplan.md` into
  [`docs/execution_phases/`](execution_phases/README.md)
- Researched every external dependency in the masterplan against current (Aug 2026) vendor
  reality; found five assumptions that no longer hold (see Key Decisions)
- Chose Supabase over Neon for Postgres + Auth
- **Resolved D1/D2 to Exa** (single provider, $10/mo recurring credit); dropped Serper and Tavily
- **Resolved D4 to Fly.io as primary** — Hetzner VPS option removed entirely, not deferred
- **Resolved D3** — arq dropped from the masterplan; the `SKIP LOCKED` executor is the queue
- Confirmed Supabase for v1 with Postgres-on-Fly as the named fallback

## Ongoing Work

- [ ] Phase 00 — Foundation, Contracts & CI
- [ ] Phase 01 — Dependency Validation Spike (start credential requests first)

## Completed Milestones

- [x] Project documentation structure created
- [x] Masterplan decomposed into 16 modular, testable execution phases

## Key Decisions

### Deviations from the masterplan (verified Aug 2026)

| # | Masterplan assumption | Reality | Resolution |
|---|---|---|---|
| D1 | Brave Search API free tier | Killed Feb 2026; now $5/1k queries | **Exa** — $20 signup + $10/mo recurring free credit; single provider in v1 |
| D2 | Google CSE as fallback | Closed to new customers; retires 2027-01-01 | Dropped; Exa's monthly allowance is the zero-marginal-cost tier |
| D3 | "arq on Postgres, no Redis" | arq requires Redis — the stated config is impossible | Drop arq; the hand-rolled `SKIP LOCKED` executor already is the queue |
| D4 | Fly.io fits "near zero cost" | No free tier since 2024 | **Fly confirmed primary** — already in use, measured under $5/mo. "Near zero" was wrong; a few dollars is accepted |
| D5 | Reddit as a routine Tier-2 source | Self-service registration closed; 2–4 week manual approval | Reddit off the critical path, feature-flagged; HN + GitHub + Stack Exchange are the backbone |

### Supabase over Neon

Chosen because it provides Postgres **and** OAuth in one free tier. Supabase Auth's
`auth.users` / `auth.identities` map almost exactly onto masterplan §4.3, including
cross-provider account linking — which removes most of Phase 12's auth work.

Two constraints, both mitigated in the phases:
- **7-day idle pause** → static homepage on Vercel (touches no DB) + GitHub Actions keepalive cron
- **500 MB ceiling** → TTL eviction, benchmark-source pinning, and a `quote_context` window
  denormalised onto `claims` so drill-down survives source eviction

Trade-off accepted: no DB branching for CI (compensated by ephemeral Postgres in Docker).

**Decided: keep Supabase for v1.** Escape hatch if either constraint bites: self-hosted **Postgres on Fly**
alongside API and worker — no pause, no ceiling, one less vendor. Cost of that move is auth (Authlib +
OAuth flow return to Phase 12). Do *not* do the hybrid — self-hosted Postgres plus Supabase-for-auth-only
splits `user_profiles` from `auth.users` across two databases and breaks the Phase 00 FK.
Migration trigger: Phase 14 measures per-run storage well above ~1.2 MB, or the keepalive proves unreliable.

### Search: single provider, deliberately

Exa is the only search provider in v1. This gives up the index independence the earlier
Serper + Tavily pairing had. Accepted because the domain retrievers (HN Algolia, GitHub,
Stack Exchange, Wayback, package registries) are independent of Exa entirely, so an outage
degrades discovery rather than stopping a run.

**Search cost changed shape:** metered per-query → fixed monthly allowance. It cannot produce a
surprise bill, but it *can* run out mid-month. Consequence: budgets track **credits, not query
counts**, and `GLOBAL_RUNS_PER_DAY` now protects the allowance rather than the wallet.

### Cost model

**under $5/month fixed**, ~**$0.03/run** (LLM only; search is $0 inside the Exa allowance).
Path-guessing hit rate is the dominant lever — measured in Phase 01 and Phase 03. If it comes in
low, the cost is quota rather than dollars: search volume ~4× means the sustainable run ceiling drops ~4×.

## Next Steps

1. **Start the two slow credential requests today** — Reddit (2–4 weeks, manual approval)
   and Product Hunt. Everything else is instant.
2. Begin [Phase 00](execution_phases/phase-00-foundation-contracts-ci.md) — repo scaffold,
   schema, typed contracts, CI.
3. Run [Phase 01](execution_phases/phase-01-dependency-validation-spike.md) before building
   on any vendor. It closes masterplan open item #3 (is Playwright needed?) and settles the
   search provider bake-off with measured numbers.

## Open Items Carried From the Masterplan

| # | Item | Closes in |
|---|---|---|
| 1 | All quota and budget values | [Phase 14](execution_phases/phase-14-benchmark-calibration.md) |
| 2 | The ten benchmark queries + hand-verified ground truth | [Phase 14](execution_phases/phase-14-benchmark-calibration.md) |
| 3 | Whether Playwright is needed at all | [Phase 01](execution_phases/phase-01-dependency-validation-spike.md) |
