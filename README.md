# AI Product Investigator

Type a product idea, get an evidence-backed discovery report in under three
minutes, where **every sentence in the report traces to a dated character
span in a fetched page** — click any sentence and the exact span highlights in
the source text. Open-source, self-hostable, designed to run for a few dollars
a month.

- Live app: frontend at https://ai-pi-kohl.vercel.app (API: https://ai-product-investigator.fly.dev)
- Full spec: [`ai-product-investigator-masterplan.md`](ai-product-investigator-masterplan.md)
- Build history, phase-by-phase: [`docs/execution_phases/`](docs/execution_phases/README.md)
- Honest measured numbers: [`docs/benchmark.md`](docs/benchmark.md), [`docs/tuning.md`](docs/tuning.md)
- Operations: [`docs/runbook.md`](docs/runbook.md)

---

## The one guarantee

A claim is **only ever written if its quote is found verbatim in stored page
text** (`source_text.find(quote)` — no fuzzy matching, an ambiguous quote is
dropped, never guessed). Because that is the mechanism, every prose sentence
in a report can cite a real character span, and the drill-down is the demo:
click any cited sentence, the exact `[start, end)` span highlights in the
fetched source.

Everything else in the architecture exists to make that one guarantee cheap
to keep: a closed claim vocabulary, a computed (never model-generated)
confidence score, contradiction detection as SQL over typed claims, and
synthesis that is constrained to cite findings.

## Architecture

```
free text -> Interpret (ResearchBrief) -> Plan (validated task DAG)
  -> Executor (asyncio DAG over a Postgres task table, SKIP LOCKED leasing)
    -> fetch/search/domain-retriever tasks -> claim extraction (span-bound)
      -> entity resolution -> grading + confidence + contradictions
        -> synthesis (citation-constrained) -> Report (persisted, SSE)
```

| Layer | Choice | Why (decision log: masterplan §12) |
|---|---|---|
| Orchestration | Hand-rolled asyncio DAG over a Postgres `tasks` table — `SELECT … FOR UPDATE SKIP LOCKED`, lease columns, `Executor.submit(plan) -> AsyncIterator[Event]` as the only entry point | **D1.** Fan-out of independent stateless tasks needs no graph engine; the executor *is* the queue (no Celery, no Redis, no arq) — one fewer service, one fewer bill, and run state is queryable with plain SQL |
| LLM | One model — `deepseek/deepseek-v4-flash` via OpenRouter, strict JSON schema + Pydantic validation on every response | **D2.** At ~$0.06/run a routing ladder optimises a problem that doesn't exist; a swap is a config change behind the gateway |
| Search | Exa primary; path-guessing fetches `/pricing` directly instead of searching for it | **D3.** Most of a run's "searches" are deterministic path guesses, not searches — discovery is reserved for search, keeping runs to ~8 queries |
| Entity identity | Scheme-prefixed keys (`web:`, `gh:`, `npm:`, `pypi:`, …) with PSL private domains enabled | **D4.** Products legitimately live at `foo.fly.dev` or in a repo with no site; without PSL awareness every Fly-hosted product collapses into one `fly.dev` entity |
| Confidence | Computed formula over grade, distinct-source count, age, contradiction status | **D5.** A model-emitted `0.82` is decoration; the formula is deterministic, tunable, and defensible to an interviewer |
| Contradictions | A SQL `GROUP BY` over typed claims (grade D excluded), highest-grade-wins resolution, losers retained | **D6.** It's a GROUP BY — free, deterministic, can't hallucinate a contradiction, and the entire reason the claim vocabulary is closed |
| Span binding | Verbatim quote + local `str.find`; model offsets never trusted | **D7.** Models fabricate offsets confidently; `find()` either succeeds or the claim is dropped |
| Auth | Supabase Auth (Google + GitHub OAuth), JWT verified locally against JWKS | Login to run, public benchmark reports to read — no anonymous runs (search credits are the scarce resource), no BYO key on a public site |
| Crawl | httpx + trafilatura; Playwright deferred behind a flag | **D10.** ~80% of target pages are static HTML; Firecrawl-everywhere is the biggest avoidable cost in this product class |
| Deploy | Fly.io (API + worker machines, one image) + Supabase (Postgres + Auth) + Vercel (frontend) | Two machines, one managed database, TLS terminated by Fly. Free tiers cover the rest |
| CI | GitHub Actions replaying cached HTTP fixtures, `temperature: 0` | Deterministic, free regression runs |

The pipeline runs as an asyncio executor with **partial failure as the normal
case** — a dead branch reduces `coverage`, it doesn't fail the run, and
`coverage` is reported separately from confidence so the report says what it
didn't verify.

## Injection resistance (stated explicitly)

Prompt injection arrives from crawled pages, not from users — a competitor's
site can carry text aimed specifically at research agents. This system is
**structurally resistant rather than filter-dependent**:

- Page content only ever enters a **schema-constrained extraction prompt**
  whose output space is the closed claim vocabulary. It never reaches a
  free-text prompt — untrusted content is appended as an entity-escaped,
  tagged `<untrusted>` block, never interpolated into instructions
  (proven adversarially in tests).
- Output that fails validation is **dropped**, with a counted drop reason.
- Any quote not literally present in the stored source text is **discarded**.

There is no path from page text to free-text generation. The best a hostile
page achieves is an ordinary, correctly-cited, contradictory claim — and the
SQL contradiction detector is built to surface exactly those. Worked example:
`tests/fixtures/extraction/adversarial_*`.

## Honest benchmark numbers (Phase 14)

Dated **2026-08-08**. Ten hand-built queries (`bench/queries/q01.yaml`–`q10.yaml`),
six tuning / four held-out, run **once each against the real pipeline** —
real OpenRouter, real Exa, real vendor sites, no mocking. Methodology, raw
snapshots (`bench/results/2026-08-08/`), and every calibration decision:
[`docs/benchmark.md`](docs/benchmark.md) and [`docs/tuning.md`](docs/tuning.md).

These numbers are published in full — **including the bad ones** — because
this benchmark *is* the definition of every quality claim this README makes.

**What held up — the hard metric:**

- **Sentence binding rate: 100% on every single run, no exceptions.** The one
  pass/fail metric (below 100% means the citation enforcement is broken, and
  that is a bug, not a tuning target).
- **Precision: 100%.** Not one `known_absent` domain ever leaked into a report
  across all ten runs — every "wrong" competitor found was still a real, live,
  artifact-verified product (masterplan Rule 2 working as designed).

**What did not — recall was 0% on the tuning set, honestly reported:**

| set | recall | precision | fact acc. | binding | contradiction | cost | duration |
|---|---|---|---|---|---|---|---|
| tuning (6) mean | **0.00** | 1.00 | 0.17 | 1.00 | 4/6 fired | $0.0621 | 224.2s |
| held-out (4) mean | **0.25** | 1.00 | 0.25 | 1.00 | 4/4 fired | $0.0649 | 270.2s |

The held-out recall mean is *entirely* one vacuous case (q10, a deliberately
near-empty category whose ground truth carries no `must_include`, so zero
fabricated competitors scores perfect by definition); q03/q05/q06 each
independently reproduce the tuning-set's 0% against real ground truth.

**Why recall is zero — three distinct, traced causes, two fixed and one open:**

1. **Planner misjudgement** (q01, q03): GitHub OSS discovery engaged for
   plainly mainstream categories, burning the whole run budget on curated-list
   repos before real candidates' pricing was fetched. **Fixed** (2026-08-10):
   `plan_dag.md`'s `consider_oss` guidance rewritten + a mechanical backstop
   (`discover.py` never seeds from `awesome-*` repos). Re-measured: q01 went
   from 0 to 4 real, verified competitors.
2. **Long-tail over household names** (q04, q07, q09): discovery consistently
   surfaced small/indie products over the market leaders the ground truth
   names — a real discovery-relevance gap, precision stayed perfect. **Partly
   fixed** (2026-08-10): wider discovery window + Exa `mode="auto"`; q04 now
   discovers and profiles expensify.com.
3. **Pricing-triple completion** (open, located precisely): even when the right
   competitors are found, `value_type_mismatch` mass-drops pricing claims
   (extractor emits prose for booleans/numbers — 54 in one q01 run) and
   `profile_product` times out on JS-heavy pricing pages. This is the single
   highest-leverage remaining fix; it owns a future phase.

**Other honest notes:** coverage reads 0.00 on every run (no domain-age or
install-count signal source exists for `web:` entities yet — a known Phase 10
finding, confirmed at scale, not a bug in this run); synthesis
(MVP/feature-gaps/risks) never fired on any run (the community-mining gate
was never cleared); one trap query's contradiction detector fired but on a
false positive, not the researched trap (the `ValueKind.LIST` contradiction
bug — **fixed** in the Phase 14 follow-up). Zero-spend CI replay is
real for q08 but not yet a hard-green gate for all ten queries (cache-fixture
gaps; `bench.yml` ships with `continue-on-error` and a top-of-file explanation
rather than lying).

## Deviations from the original plan

The masterplan is the authority on *why*; the reality of building it changed
several things, each recorded with its reason in
[`docs/execution_phases/README.md`](docs/execution_phases/README.md):

- **Fly's free tier is gone** (2024) → "near zero cost" became "small and
  fixed" (~$6–8/mo for two `shared-cpu-1x` machines). Fly stays: a known-
  working platform beats a $2-cheaper VPS we'd have to maintain ourselves.
- **Brave's free tier killed, Google CSE closed to new customers** (2026) →
  Exa is the *only* search provider. The lost two-vendor redundancy is
  acceptable because Exa only degrades discovery; domain retrievers are
  independent.
- **Reddit dropped** — its manual 2–4 week app-approval process made it
  infeasible; HN + GitHub + Stack Exchange are the community backbone.
- **Supabase over Neon** — Postgres **and** OAuth in one free tier, and
  `auth.users`/`auth.identities` map almost exactly onto the plan's account
  model (one email across Google + GitHub → one account), deleting an entire
  Phase 12 subsystem (Authlib, session cookies, identity merge). Two
  constraints accepted: free projects pause after 7 days idle (mitigated by a
  statically-rendered homepage + a monitored GitHub Actions keepalive cron)
  and a 500 MB storage ceiling (mitigated by nightly eviction of expired
  unpinned source text + benchmark-source pinning + size alerts).
- **`arq` dropped** — it would have required Redis; the hand-rolled `SKIP
  LOCKED` executor already *is* the queue.
- **Playwright deferred behind a feature flag** — measured 88% static-HTML hit
  rate and 1/5 Playwright recoveries (3/4 of the rest were Cloudflare walls
  Playwright doesn't beat either) made it not worth the complexity.
- **The worker is a recovery/sweep + maintenance machine, not a task runner.**
  Runs currently execute inside the API process (a single-worker design the
  executor was built for). A second machine exists with the same image and a
  different entrypoint, and handing task execution over is logged as future
  work — the phase doc's "a heavy run cannot starve the API" premise is not
  yet true. See [`docs/tracker.md`](docs/tracker.md).

## Self-hosting

The repo is public and the keys are yours. To run your own instance:

```bash
# Backend (Python 3.12, managed by uv)
uv sync --extra dev
cp .env.example .env          # fill in DATABASE_URL, OPENROUTER_API_KEY, EXA_API_KEY, GITHUB_TOKEN
make db-up                    # local Postgres + pgvector (docker compose)
make migrate
uv run python -m api.cli run "AI expense tracker for freelancers"   # a real run
make check                    # ruff + mypy --strict + pytest (== CI gate)
```

For a production deployment, see:
- [`docs/runbook.md`](docs/runbook.md) — topology, secrets, alerts, restore/rollback
- [`deploy/.env.prod.example`](deploy/.env.prod.example) — every production
  variable, placeholder-only
- [`deploy/backup.sh`](deploy/backup.sh) — nightly `pg_dump` → R2
- [`fly.toml`](fly.toml) / [`fly.worker.toml`](fly.worker.toml) / [`Dockerfile`](Dockerfile)
  — one multi-stage image, two machines, different entrypoints

Frontend (own npm toolchain, deploys itself via Vercel): [`web/README.md`](web/README.md).

## Cost model (measured, not estimated)

- **Per run:** $0.062 mean / $0.078 p95 (Phase 14, real traffic).
- **Fixed:** two Fly machines ~$6–8/mo; Supabase, Vercel, Langfuse, and the
  GitHub Actions crons on free tiers.
- **Guardrails (layered, from the inside out):** per-run budget weight + dollar
  cap → per-user/global daily quotas → kill switch → vendor-side hard caps
  (OpenRouter has one in its dashboard; **Exa does not offer a dashboard
  spend cap**, so the app-level `search_credit_usage` ledger is the
  enforcement layer there — documented decision, 2026-08-11, `docs/runbook.md`).

Every quota knob is a *derived, dated* value from Phase 14 benchmark data
(`docs/tuning.md`), not a guess.
