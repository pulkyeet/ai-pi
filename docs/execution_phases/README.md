# Execution Phases — AI Product Investigator

Derived from [`ai-product-investigator-masterplan.md`](../../ai-product-investigator-masterplan.md).
The masterplan is the *what* and *why*, deliberately timeline-free. This directory is the *how* and *in what order*.

**Last updated:** 2026-08-06

---

## How to use these docs

Each phase is a self-contained unit of work with:

- **One concrete output** — a thing you can run, demo, or point at. Not "the extraction layer is done" but `python -m api.cli extract <url>` prints validated claims with verified spans.
- **A hard exit gate** — a checklist that is objectively true or false. No phase is "mostly done".
- **Thorough tests** — every phase ships its own tests and cannot exit with a red suite. Test strategy is specified per phase, not left to taste.
- **Explicit dependencies** — what must exist first, what this unblocks.

Phases are sized to be independently reviewable. If a phase feels like it needs a week of uninterrupted work, it was scoped wrong — split it.

**Read [Phase 00](phase-00-foundation-contracts-ci.md) and [Phase 01](phase-01-dependency-validation-spike.md) before writing any code.** Phase 01 in particular exists to kill deviation risk early: it validates every external dependency against reality before anything is built on top of it.

---

## Phase index

| # | Phase | Depends on | Concrete output |
|---|---|---|---|
| 00 | [Foundation, Contracts & CI](phase-00-foundation-contracts-ci.md) | — | `make test` green; migrations up/down; typed contracts frozen |
| 01 | [Dependency Validation Spike](phase-01-dependency-validation-spike.md) | 00 | `docs/external_apis.md` with real measured limits + fixture corpus |
| 02 | [Executor Core](phase-02-executor-core.md) | 00 | Executor survives chaos suite: worker kill, lease expiry, budget cap |
| 03 | [Fetch, Text Extraction & Source Cache](phase-03-fetch-source-cache.md) | 00, 01 | `fetch_source(url)` → cached `Source`; path-guessing hit-rate measured |
| 04 | [Search & Domain Retrievers](phase-04-search-domain-retrievers.md) | 00, 01, 03 | Unified retriever interface; per-run search budget accounting |
| 05 | [LLM Gateway](phase-05-llm-gateway.md) | 00, 01 | `llm.structured(schema, ...)` with cost + cache telemetry |
| 06 | [Claim Extraction & Span Binding](phase-06-claim-extraction-span-binding.md) | 03, 05 | Page → validated claims, 100% span-verified; drop-rate metric |
| 07 | [Entity Resolution & Identity](phase-07-entity-resolution.md) | 00, 03 | Canonical entity keys, alias merging, maturity tiers |
| 08 | [Grading, Confidence & Contradictions](phase-08-grading-confidence-contradictions.md) | 06, 07 | Contradiction detector fires on the known trap case |
| 09 | [Interpreter & Planner](phase-09-interpreter-planner.md) | 05 | Free text → `ResearchBrief` + schema-validated task DAG |
| 10 | [Task Handlers & End-to-End Run](phase-10-task-handlers-e2e.md) | 02, 04, 06, 07, 09 | **`api run "<idea>"` produces claims in DB** — walking skeleton |
| 11 | [Findings, Synthesis & Report Assembly](phase-11-synthesis-report-assembly.md) | 08, 10 | **Full report JSON matching the output contract; 100% sentence binding** |
| 12 | [API, Auth, Quotas & Guardrails](phase-12-api-auth-quotas.md) | 11 | Authenticated HTTP API with SSE, quotas, kill switch |
| 13 | [Frontend & Drill-Down UI](phase-13-frontend.md) | 12 | Web UI: live plan checklist, report, span-highlighted drill-down |
| 14 | [Benchmark Harness & Calibration](phase-14-benchmark-calibration.md) | 11 | Measured quality numbers; every TBD quota knob resolved |
| 15 | [Deployment, Observability & Cost Control](phase-15-deployment-observability.md) | 12, 13, 14 | Live public URL, traces, spend caps, cost per run dashboard |

### Dependency graph

```
00 Foundation ─┬─ 01 Dep spike ─┬─ 03 Fetch ─┬─ 06 Extract ─┬─ 08 Evidence ─┐
               │                │            │              │               │
               ├─ 02 Executor   ├─ 04 Search │              │               │
               │                │            │              │               │
               ├─ 05 LLM ───────┴─ 09 Planner│   07 Entity ──┘               │
               │                             │                              │
               └─ 07 Entity ─────────────────┘                              │
                                                                            │
   02 + 04 + 06 + 07 + 09  ──►  10 E2E run  ──►  11 Report ◄────────────────┘
                                                     │
                                    ┌────────────────┼────────────────┐
                                    │                │                │
                                12 API/Auth ──► 13 Frontend      14 Benchmark
                                    │                │                │
                                    └────────────────┴────────────────┘
                                                     │
                                            15 Deploy & Observe
```

**Two milestone gates.** Phase 10 is the walking skeleton — the first time a real query produces real claims end to end. Phase 11 is product-complete — the first report that satisfies the masterplan's output contract. Everything after 11 is surface, measurement, and operations.

---

## Deviations from the masterplan

Five masterplan assumptions no longer hold as of August 2026. Each was verified against current vendor documentation. These are folded into the relevant phases; they are collected here so the divergence is auditable rather than silent.

| # | Masterplan says | Reality (Aug 2026) | Resolution | Phase |
|---|---|---|---|---|
| D1 | "Brave Search API primary" with a usable free tier | Brave **killed the free tier in Feb 2026**. Now $5/1k queries; new accounts get $5/mo credit ≈ 1,000 queries. Existing subscribers grandfathered. | **Exa primary** — $20 signup credit plus **$10/mo recurring free usage**. Neural search, strongest exactly where this project is weakest (thin, badly-keyworded discovery queries). Decided; the Phase 01 bake-off now measures Exa's headroom rather than choosing a vendor. | [01](phase-01-dependency-validation-spike.md), [04](phase-04-search-domain-retrievers.md) |
| D2 | "Google Programmable Search (100/day) as fallback" | Google CSE JSON API is **closed to new customers** since 2025 and **fully retires 2027-01-01**. | Dropped entirely. Exa's recurring monthly allowance is the zero-marginal-cost tier. No second provider ships in v1 — see the note below. | [01](phase-01-dependency-validation-spike.md), [04](phase-04-search-domain-retrievers.md) |
| D3 | "Worker: arq on the same Postgres. **No Celery, no Redis**" | **arq requires Redis** — it stores queue metadata and results in Redis. The stated configuration is impossible. | Drop arq. The hand-rolled `SELECT … FOR UPDATE SKIP LOCKED` executor from masterplan §4.2 already *is* the queue; arq was redundant. No Redis anywhere. | [02](phase-02-executor-core.md) |
| D4 | "Deploy: Fly.io app plus worker" fitting a "near zero cost constraint" | Fly.io **removed its free tier in 2024**. Realistic app + worker + egress lands at **$5–12/mo**. | **Fly.io confirmed as primary** — already in use on this account, measured under $5/mo for a comparable workload. "Near zero cost" was wrong; a few dollars a month is the real number, and it is accepted. Frontend on Vercel free. | [15](phase-15-deployment-observability.md) |
| D5 | "Reddit: search only, never bulk" as a routine Tier-2 source | Reddit's free tier (100 QPM, non-commercial) still exists, but **self-service app registration is closed** — new OAuth credentials require manual approval, typically 2–4 weeks. | Reddit **dropped entirely**. The manual approval process was infeasible, so no Reddit integration ships; HN Algolia + GitHub + Stack Exchange are the community-mining backbone. | [01](phase-01-dependency-validation-spike.md), [04](phase-04-search-domain-retrievers.md) |

**A note on running one search provider.** The original D1/D2 resolution paired Serper (Google-backed index) with Tavily (its own index) specifically so a single vendor outage or index gap could not blind the system — that was the surviving half of the masterplan's Brave-plus-Google reasoning. Exa-only gives that up. The judgement is that it is worth it: Exa's recurring free credit makes it the only provider that is both zero-marginal-cost *and* good at thin queries, and the domain retrievers (HN Algolia, GitHub, Stack Exchange, package registries, Wayback) are independent of it entirely — so an Exa outage degrades discovery rather than stopping a run. Revisit if [Phase 01](phase-01-dependency-validation-spike.md) measures thin-query recall below the bar, or if [Phase 14](phase-14-benchmark-calibration.md) shows the $10/mo allowance is the binding constraint on `GLOBAL_RUNS_PER_DAY`.

### Sixth change: Supabase over Neon

Not a masterplan error — the masterplan said "Neon or Supabase". Choosing **Supabase**, because it collapses two services into one:

- **Postgres + OAuth in one free tier.** Supabase Auth handles Google and GitHub natively, and its `auth.users` / `auth.identities` tables map almost exactly onto the masterplan's §4.3 schema — including one-account-per-email linking across providers. This removes Authlib, the OAuth callback dance, session-cookie handling, and identity-merge logic from [Phase 12](phase-12-api-auth-quotas.md).
- **pgvector available**, so the complaint near-duplicate path in the masterplan is unaffected.

Two constraints, both mitigated in the phases:

1. **Free projects pause after 7 days idle** (tightened Feb 2026). Mitigations: benchmark reports are statically rendered on Vercel, so the public homepage never touches Postgres; plus a GitHub Actions cron every 3 days issues a keepalive query. See [Phase 15](phase-15-deployment-observability.md).
2. **500 MB database ceiling.** `sources.extracted_text` dominates (~0.4 MB/run → ~1,250 runs, measured 2026-08-10). Mitigations: TTL eviction of non-benchmark source text; benchmark sources pinned non-evictable; and a **quote context window** (±2 KB around each cited span) denormalised onto `claims`, so drill-down survives source eviction. See [Phase 00](phase-00-foundation-contracts-ci.md) and [Phase 03](phase-03-fetch-source-cache.md).

What you give up versus Neon: database branching for CI. Phase 00 compensates with ephemeral Postgres in Docker for the test suite, which is faster than branch-per-PR anyway.

**Decided: keep Supabase, with a named escape hatch.** Both constraints above are accepted for v1 — the keepalive cron handles the pause, eviction handles the ceiling. The fallback, if either becomes a real operational problem, is **self-hosted Postgres on Fly** alongside the API and worker: no pause, no ceiling, and one less vendor. The cost of that move is auth — Supabase Auth is doing Google + GitHub OAuth for free, and dropping it puts Authlib, the OAuth callback flow, session cookies, and identity merging back into [Phase 12](phase-12-api-auth-quotas.md). Do not take the hybrid (self-hosted Postgres + Supabase for auth only): it splits `user_profiles` from `auth.users` across two databases and breaks the FK that [Phase 00](phase-00-foundation-contracts-ci.md) is built on. Migration trigger: Phase 14 measures per-run storage materially above ~0.4 MB (it measured ~0.4 MB, so the escape hatch is not triggered on storage grounds), or the keepalive proves unreliable in practice.

---

## Cost model

Target: **under $10/month fixed, under $0.05 per run.**

**Fixed monthly**

| Item | Choice | Cost |
|---|---|---|
| App + worker compute | Fly.io (app + worker machines), already in use | <$5 |
| Postgres + Auth | Supabase free tier | $0 |
| Frontend | Vercel Hobby (Next.js) | $0 |
| Observability | Langfuse Cloud Hobby — 50k units/mo ≈ **770 runs/mo** | $0 |
| Bot filter | Cloudflare Turnstile | $0 |
| CI | GitHub Actions (public repo) | $0 |
| Search | Exa — $10/mo recurring free credit (plus $20 one-time signup) | $0 |
| **Total fixed** | | **<$5/mo** |

**Marginal, per run**

| Item | Estimate | Cost |
|---|---|---|
| LLM (DeepSeek V4 Flash via OpenRouter, ~$0.09/M in, ~$0.18/M out, ~$0.02/M cache read) | ~60 pages × ~4k tok in, ~30k tok out | ~$0.03 |
| Search (Exa) | 6–10 queries after path-guessing | $0 against the monthly credit |
| Domain retrievers (GitHub, HN Algolia, Wayback, npm/PyPI, Stack Exchange) | free APIs | $0 |
| **Total per run** | | **~$0.03** |

**Search cost changed shape, not just size.** Serper was metered per query — every run cost a predictable fraction of a cent, and spend scaled smoothly. Exa is a monthly allowance, which means search is free until it abruptly is not. It cannot produce a surprise bill, but it *can* run out mid-month and take live runs down with it. Two consequences: the per-run search cost above is $0 only while inside the allowance, and `GLOBAL_RUNS_PER_DAY` is now the knob that protects the allowance rather than the budget. The masterplan §8.2 derivation still applies unchanged — `(monthly_credits / 30) / searches_per_run_p95` — but the numerator is now Exa's credit, and [Phase 04](phase-04-search-domain-retrievers.md) must track allowance consumption, not just query count.

Path-guessing (masterplan §7: fetch `/pricing` rather than searching for it) is the dominant cost lever — it is what keeps search at ~8 queries per run instead of ~40. [Phase 03](phase-03-fetch-source-cache.md) measures its real hit rate; if that number comes in low, search volume roughly quadruples and the allowance becomes the binding constraint far sooner — so the [Phase 14](phase-14-benchmark-calibration.md) quota maths must be redone.

All quota knobs stay `TBD` until [Phase 14](phase-14-benchmark-calibration.md) measures real per-run numbers. Guessing them earlier is exactly the mistake masterplan §8.2 warns against.

---

## Global conventions

### Definition of done

A phase is done when **all** of these hold:

1. Every item in that phase's exit-criteria checklist is objectively satisfied.
2. `make check` passes: `ruff` (lint + format), `mypy --strict` on `src/api/`, `pytest` green.
3. New code has tests colocated per the layout below, and coverage on the phase's own modules is ≥ 85% lines.
4. No network access in unit tests. Integration tests that need network are marked `@pytest.mark.live` and excluded from the default run.
5. `docs/tracker.md` is updated with the phase outcome and any decision that diverged from this plan.

### Test layout

```
tests/
├── unit/           # pure functions, no I/O. Fast (<5s total). Always run.
├── integration/    # real Postgres (docker), replayed HTTP fixtures. Run in CI.
├── live/           # real external APIs. Marked @pytest.mark.live. Manual + nightly.
├── fixtures/
│   ├── cassettes/  # VCR.py recordings, committed
│   └── pages/      # raw HTML + expected extraction, committed
└── conftest.py
```

Three test kinds, used deliberately:

- **Unit** — deterministic logic: confidence formula, entity-key derivation, span binding, budget arithmetic. These should be the bulk of the suite.
- **Integration** — anything touching Postgres or HTTP. HTTP is replayed from committed cassettes so CI is free and deterministic; Postgres is a real container, never a mock or SQLite.
- **Live** — smoke tests against real vendors, proving a cassette hasn't gone stale. Nightly, allowed to fail loudly without blocking a PR.

Property-based tests (`hypothesis`) are specified where the input space is large and the invariant is crisp — URL canonicalisation, span binding, lease state machines. Those cases are called out per phase.

### Determinism

CI runs the LLM at temperature 0 against recorded fixtures. No phase may depend on live model output to pass its tests. The extraction cache (keyed `content_hash + extractor_version`) makes benchmark replay free — see [Phase 06](phase-06-claim-extraction-span-binding.md).

### Naming and structure

- Python package root is `src/api/`, installed editable via `pyproject.toml`.
- One module per concern; no `utils.py`.
- All external-boundary data is parsed into a Pydantic model before it is used. Raw dicts do not cross module boundaries.
- Every table has a migration; no schema change lands without one. Migrations are reversible.
- Every LLM prompt lives in a versioned file under `src/api/prompts/`, never inline in code — so `extractor_version` is meaningful and cache invalidation is deliberate.

### Instrumentation from day one

Phase 00 wires structured logging and an OpenTelemetry tracer. Every subsequent phase emits spans for its own work. This is not deferred to Phase 15 — retrofitting observability at the end is how you end up unable to explain a cost regression.

---

## Sources

Vendor facts above were verified against:

- [Brave Search API — free tier removal](https://www.implicator.ai/brave-drops-free-search-api-tier-puts-all-developers-on-metered-billing/) and [current pricing](https://costbench.com/software/ai-search-apis/brave-search-api/)
- [Google Custom Search JSON API — overview and closure](https://developers.google.com/custom-search/v1/overview)
- [arq — job queuing in python with asyncio and redis](https://github.com/python-arq/arq)
- [Fly.io pricing after the free tier](https://www.saaspricepulse.com/blog/flyio-free-tier-2026)
- [Neon vs Supabase free tier comparison](https://agentdeals.dev/neon-vs-supabase)
- [Langfuse pricing](https://coverge.ai/blog/langfuse-pricing)
- [Serper / Tavily / Exa free tiers](https://parallel.ai/articles/best-free-web-search-api)
- [DeepSeek V4 Flash pricing on OpenRouter](https://openrouter.ai/deepseek/deepseek-v4-flash/pricing)
