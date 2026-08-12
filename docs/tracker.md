# Execution Tracker

Last Updated: 2026-08-12

## Current Status

- **Phase 15 (Deployment, Observability & Cost Control) — deployed and operationally verified.**
  Fly API/worker, Supabase, Vercel, Langfuse, and R2 are live. The deploy pipeline and nightly
  keepalive/maintenance/backup workflow are green; the remaining browser/data checks are listed
  below. Everything the phase doc's Deliverables list and storage-management sections need that the
  codebase didn't already have:
  - **Deploy artifacts:** multi-stage `Dockerfile` (pinned base-image digests, non-root `app`
    user), `fly.toml` (API machine) + `fly.worker.toml` (worker machine, same image, different
    entrypoint — managed via `fly machine run/update --config fly.worker.toml`), `deploy/.env.prod.example`
    (placeholders only), `deploy/backup.sh` (nightly `pg_dump` → gzip → R2, 30 dumps retained),
    `.github/workflows/deploy.yml` (check → build+push → **migrations → worker → API** → health
    check → rollback on failure), `.github/workflows/keepalive.yml` (nightly cron: keepalive +
    maintenance + backup, failure = GitHub's native failed-workflow notification).
  - **Backend:** `RUN_TIMEOUT_S` is now wired for real — `executor.submit(run_timeout_s=…)` stops
    claiming new work past the deadline via a new `lease.skip_rest` (`skipped, reason='run_timeout'`),
    in-flight tasks still finish, report path still runs (tuning.md's "no consumer wired" note closed).
    `src/api/maintenance.py` (`python -m api.maintenance`) runs the nightly storage jobs:
    `cache.evict_expired` (unpinned `sources.extracted_text`), new `prune_expired_events` (30-day
    `run_events` window), new `pin_benchmark_sources` (sources cited by `is_benchmark` runs), and a
    `pg_database_size` size query. `src/api/worker.py` (`python -m api.worker`) is the worker machine
    entrypoint: periodic `lease.sweep_expired` recovery sweep + maintenance pass + health log line.
    Migration `0012_run_stats` makes the extraction drop-rate durable (`run_stats` written at
    run-finish by both `cli.run_query` and `web.runner.run_pipeline`). **`GET /metrics`** (authenticated)
    exposes the runbook's nine-alert set: runs/day, cost/run, search spend MTD, sentence-binding rate
    (**the only page-immediately alert**, breaches *below* 100%), extraction drop rate, p95 latency,
    task failure rate, DB size (70%/85% of 500 MB).
  - **Docs:** `README.md` created (repo root — the phase doc's architecture/decisions/injection/
    honest-benchmark-numbers/deviations/self-hosting section; the file did not previously exist despite
    the masterplan referencing anchors in it). `docs/runbook.md` — the nine-alert table with thresholds,
    cap-hit/vendor-outage/DB-near-limit/restore/rollback/key-rotation procedures, the **Exa
    allowance-as-ceiling decision (2026-08-11)** and the full secret inventory.
  - **Verification:** `make check` green — **819 passed, 16 deselected, 96% coverage** (new:
    `test_maintenance.py` 5 tests, `test_metrics.py` 3 tests, `test_run_timeout_skips_remaining_tasks`).
    Docker build succeeds (non-root, all modules import under the container's venv). `deploy/backup.sh`
    `bash -n` clean; both workflow YAMLs parse; both fly configs pass `fly config validate`.
    **Migrations verified against the real Supabase project** (0001→0012 clean over the session
    pooler; the 0001 `auth`-schema-exists guard is a no-op and `user_profiles` resolves to
    `auth.users`) and against a real-shaped `auth` schema.
  - **Deviations (reasons in the Phase 15 Recent Activities entry below):** the worker machine is a
    recovery-sweep + maintenance machine, not a task runner (runs still execute in the API process —
    single-worker design, the phase doc's "heavy run cannot starve the API" premise is not yet true);
    alerts ride GitHub's native failure notifications + a runbook habit rather than an external
    alerting service; Exa's missing dashboard spend cap is documented as an allowance-as-ceiling
    decision rather than a silent gap. Every "logged here, not fixed here" finding in
    `docs/benchmark.md`/`docs/tuning.md` was implemented,
  test-covered, and re-validated live on three tuning queries (q01/q04/q08, ~$0.37 real spend):
  (1) `consider_oss` planner guidance rewritten in `plan_dag.md` **plus** a mechanical backstop —
  `discover.py` never seeds from `awesome-*` curated-list repos again (`_is_github_list_repo`
  filters name/description; when `consider_oss` is true it searches the category itself,
  `"<cat> in:name,description stars:>100"`); (2) discovery widened to `DISCOVERY_SEARCH_LIMIT=20`
  and Exa switched to `mode="auto"`; (3) `pricing.model` gains `free` and `build_competitors`
  accepts `entry_usd_month=0.0` for it (`ReportView` renders "Free"); (4) `_is_contradictory`
  skips `ValueKind.LIST` attributes (the 8/8 contradiction false-positive class); (5) new 06b —
  Exa snippets are quoteable grade-C sources, `pricing.*` excluded; (6) **`merge_alias` FK crash
  fixed** (repoint claims onto the canonical before the delete — q08's discovery crashed on it
  live pre-fix, re-ran clean post-fix). `make check` **806 tests, 96.06% coverage**; web vitest 35
  green. **Honest result: discovery improved exactly as intended** (q01: 0 → 4 real verified
  competitors; q04: expensify.com discovered+profiled; q08: real OSS surfaced) **but recall still
  reads 0.00** — a *second* bottleneck now blocks the report: `value_type_mismatch` mass-drops
  pricing claims (54 in one q01 run; extractor emits prose where `true`/a number is required,
  traced via 06a), `profile_product` times out at `timeout_s=90` on JS-heavy pricing pages, and
  the recall metric can't credit a `gh:` discovery against `web:` ground truth (q08's mkdocs).
  All documented with numbers in `docs/benchmark.md`/`docs/tuning.md`; the fix set is committed.
  Decision 06a verdict: no quote-length floor — the traced drop causes are entity-decoding and
  prompt/schema compliance, not short quotes. Decisions 06b–11 closed per the ELI5 walkthrough
  (06b yes; the rest keep-current, documented). Reddit dropped as a source: its manual 2–4 wk
  app-approval process made it infeasible, so the codebase carries no Reddit integration.

- **Phase**: Phase 12 complete — API, Auth, Quotas & Guardrails. `src/api/web/` puts an authenticated
  FastAPI HTTP layer in front of everything `api.cli`/`api.synth` already do end to end: Supabase JWT
  verification (local, JWKS-cached), atomic per-user/global daily quotas, an in-process concurrency
  queue with visible position, a database-flag kill switch, Cloudflare Turnstile, SSE streaming with
  lossless `Last-Event-ID` reconnect, public benchmark reports, and drill-down claims. `make check`:
  **729 tests, 0 failures, 96.28% coverage overall**; every `src/api/web/` module 91–100% (`main.py`,
  the `uvicorn.serve()` production entrypoint, is intentionally out of the coverage scope — see
  `pyproject.toml`'s `[tool.coverage.run]` comment — the same treatment `api.cli`/`api.config`/`api.db`
  already get). New: migration `0010` (`system_state` kill-switch singleton, `runs.keywords`/
  `runs.disambiguation_fields`, a `needs_input` status value, quota indexes), `src/api/web/{app,auth,
  quota,killswitch,turnstile,sse,errors,runner}.py`, `src/api/web/routes/{runs,reports,health}.py`,
  `src/api/web/main.py` (production entrypoint). Endpoints: `POST /runs`, `GET /runs/{id}`,
  `GET /runs/{id}/events` (SSE), `GET /runs/{id}/report.json`, `GET /runs/{id}/claims/{claim_id}`,
  `PATCH /runs/{id}` (disambiguation resolution), `GET /reports/benchmark`, `GET /health`.
- **A real bug found and fixed while writing the quota atomicity test.** The masterplan §8.3 SQL
  sketch (`INSERT ... SELECT ... WHERE count(*) < quota`) reads atomic but isn't, under Postgres's
  default `READ COMMITTED`: `N` concurrent requests at a quota of `N-1` all admitted (8/8, not 7/8) —
  each simultaneous statement saw the same pre-insert count. Fixed with a `pg_advisory_xact_lock`
  (a fixed key for the global cap, then `hashtext(user_id)` for the per-user cap, always acquired in
  that order so no caller can deadlock) serializing the check before the conditional insert. See
  `api.web.quota.try_create_run`'s own docstring and `tests/integration/test_quota.py::
  test_quota_atomicity_admits_exactly_n_minus_one`.
- **A design decision surfaced before writing code, per this project's own convention**: concurrency
  admission (`ConcurrencyQueue.acquire`) happens inside the background pipeline task, not synchronously
  in `POST /runs`'s response path — `GET /runs/{id}` reports `queue_position` for a caller to poll,
  matching the phase doc's own Open Decision #2 ("Lean polling until the run starts"). Concurrency
  tracking is in-memory, per-process — the same accepted single-worker limitation Phase 02's
  `BudgetTracker` already established, logged the same way rather than silently assumed.
- **Phase**: Phase 13 complete — Frontend & Drill-Down UI. `web/` (Next.js App Router + TypeScript,
  its own npm toolchain) puts a working UI in front of everything `src/api/web/` exposes: a
  statically-rendered homepage of benchmark reports (zero backend dependency), a Supabase-authenticated
  live-run flow with a real-time plan checklist and progressive findings over the fetch-based SSE
  client, and the drill-down panel — click any cited sentence, the exact `[char_start, char_end)` span
  highlights in the source text, code-point-to-UTF-16 offset conversion handled explicitly. 33 vitest
  unit tests, 30 Playwright E2E tests (chromium + mobile-chrome), `tsc`/`eslint` clean. Extended
  Phase 12's drill-down endpoint (`grade`/`confidence`/`confidence_inputs`/`source_fetched_at`/"other
  claims from this source") and added `GET /runs/{id}/findings/{id}` — both real gaps found while
  building against the phase doc's own drill-down design, not speculative additions.
- **Phase**: Phase 14 complete — Benchmark Harness & Calibration. `bench/` (repo-root, sibling of
  `src/`, mirrors `spikes/`'s own precedent) is a ten-query benchmark with hand-verified, dated ground
  truth (`bench/queries/q01.yaml`–`q10.yaml`, six tuning / four held-out), a loader enforcing the
  staleness (60-day) and tuning/held-out-split disciplines mechanically (`bench/loader.py`), a pure
  scoring layer (`bench/metrics.py`), a live-pipeline runner (`bench/runner.py`, `--cached-only` for
  zero-spend replay — a real, unresolved gap found verifying it, see Blockers), and a CI regression
  check (`bench/regression.py`, `.github/workflows/bench.yml`). All ten queries were actually run
  against real vendors this session — not simulated. `make check`: **792 tests** (up from 730),
  **96.05% coverage overall**, stable across two consecutive full runs; `bench/loader.py` 100%,
  `bench/metrics.py` 99%, `bench/regression.py`'s non-CLI logic fully covered, `bench/runner.py`
  excluded from the coverage gate (drives real vendors/Postgres end to end, same treatment as
  `api.cli`/`api.config`). Full numbers and every calibration decision: `docs/benchmark.md`/
  `docs/tuning.md`. **Every masterplan §8.2 quota
  knob is now a derived, real value** (`.env.example`, `RUN_BUDGET_WEIGHT=70`, `RUN_BUDGET_USD=0.25`,
  `RUN_TIMEOUT_S=640`, `MAX_COMPETITORS_PROFILED=8`, `MAX_PAGES_PER_ENTITY=4`,
  `MAX_COMMUNITY_THREADS=20`, `GLOBAL_RUNS_PER_DAY=4`, `RUNS_PER_USER_PER_DAY=3`,
  `MAX_CONCURRENT_RUNS=2`, `EXA_DAILY_CREDIT_CAP_USD=0.33`, `EXA_GLOBAL_DAILY_CREDIT_CAP_USD=0.33`),
  closing masterplan §14 open items #1 and #2.
- **Focus**: Begin Phase 15 — Deployment, Observability & Cost Control. The Phase 14 follow-up
  (above) landed its owning-phase fixes and re-measured them; the residual recall gap is now
  precisely located (pricing-triple completion + the cross-scheme scoring gap) and owns its next
  phase. Phase 15's own work is unchanged: Option A topology (Vercel frontend + Fly api/worker +
  Supabase, no custom domain), `RUN_TIMEOUT_S` executor wiring, Supabase pre-deploy verification,
  keepalive/backups/eviction, Langfuse, alerts, CI/CD, runbook. See Blockers and Recent Activities.
- **Blockers**: None hard-blocking Phase 15, but three Phase 14 findings are worth reading before
  deploying behind these numbers: **(a) recall against well-known market-leader ground truth was 0% on
  every one of the six tuning queries** — not a benchmark-harness bug (precision stayed 100%, and the
  harness's own tests plus a full re-run from cache confirm the scoring is correct) but a real,
  traced discovery-layer weakness: the planner incorrectly engaged GitHub OSS discovery for
  plainly-mainstream categories on 2 of 10 runs (q01, q03 — both of this benchmark's own
  "GitHub-should-be-skipped" role queries), and even when discovery worked normally it consistently
  surfaced small/long-tail products over household names (q04, q07, q09) — Phase 09/04-shaped work,
  not fixed here, full detail in `docs/benchmark.md`. **(b) the trap query's contradiction detector
  fired, but not on the researched trap** — `helpscout.com` was never discovered, and the
  contradiction that did fire was a false positive on a legitimately multi-valued attribute
  (`product.integrations`) that `api.evidence.contradictions`'s SQL treats as single-valued — a real
  Phase 08 design gap, found by the benchmark exactly as intended, not fixed here. **(c) synthesis
  (MVP/feature-gaps/risks) never fired on any of the six tuning runs** — traced to
  `MAX_COMMUNITY_THREADS`'s per-pair floor bug (fixed at the quota-knob level, `docs/tuning.md`, but
  only partially — the deeper fix is structural, a Phase 09/10 design note). **(d) true zero-spend CI
  replay is not currently achievable** — `RobotsCache` is in-memory only and domain retrievers
  (HN, GitHub Search) have no cache layer at all, confirmed by an actual `--cached-only` re-run
  failing tasks on 5 of 6 tuning queries; `bench.yml` ships with `continue-on-error` on the affected
  steps rather than either faking green or being withheld — real Phase 03/04 work, `docs/tuning.md`
  §6. Carried forward,
  unaffected by Phase 14: the GitHub Starring endpoint's
  permanent restriction (2026-06-30 vendor lockdown), `evaluate_github_theme`'s zero real callers
  (confirmed still true — see `docs/tuning.md` §2), two frozen Phase 00 `Report` leaf fields widened in
  Phase 11, the Phase 12 Open Decision #1 (anonymous trial runs — now unblockable: real per-run cost is
  $0.062 mean, `docs/benchmark.md`, cheap enough that Phase 15 can revisit this for real), Phase 13's
  `node_key`-less SSE contract and its untested-against-live-Postgres backend extension (now exercised
  for real by every `bench.runner` invocation this session — Docker was available throughout).

## Recent Activities

### 2026-08-11 — Phase 15 (Deployment, Observability & Cost Control): code/deploy layer built

- **Deploy artifacts landed** (deliverables list in the phase doc, plus the storage-management work):
  `Dockerfile` + `.dockerignore`, `fly.toml`, `fly.worker.toml`, `deploy/.env.prod.example`,
  `deploy/backup.sh`, `.github/workflows/deploy.yml`, `.github/workflows/keepalive.yml`,
  `README.md` (new at repo root), `docs/runbook.md`, `.env.example` gained commented
  `SUPABASE_URL`/`CORS_ALLOW_ORIGINS` placeholders. Full detail in the Current Status bullet above.
- **`RUN_TIMEOUT_S` is wired, closing tuning.md §5's "no consumer wired anywhere" note.**
  `Executor.submit` gained `run_timeout_s` (`src/api/executor/core.py`); past the deadline the
  dispatch loop stops claiming new work and `lease.skip_rest` marks every still-pending/running task
  `skipped, reason='run_timeout'`; in-flight tasks are allowed to finish (their cost is already
  spent) and the report path still runs — "stop the fan-out, finish with what we have". Both
  `api.cli.run_query` and `api.web.runner.run_pipeline` pass `settings.run_timeout_s`. Tested by
  `tests/integration/test_executor.py::test_run_timeout_skips_remaining_tasks` (5 sleep tasks,
  `run_timeout_s=0.2` → `done==0`, `skipped==5`, all rows `('skipped','run_timeout')`).
- **Storage management implemented** (`src/api/maintenance.py`, `python -m api.maintenance`):
  reuses `api.retrieval.cache.evict_expired` for the nightly TTL eviction of unpinned
  `sources.extracted_text` (rows + `claims.quote_context` survive, so drill-down still works — the
  phase doc's Ops row "Eviction runs; drill-down still works on an evicted source" still needs its
  **live production check**); new `prune_expired_events` (30-day `run_events` window, was entirely
  unpruned before — `run_events` is append-only); new `pin_benchmark_sources` (sources cited by
  `is_benchmark` runs get `is_pinned=true`, idempotent — the ~12 MB benchmark set never evicts);
  `database_size_bytes` via `pg_database_size`. Covered by `tests/integration/test_maintenance.py`
  (5 tests). Runs nightly from the keepalive workflow's maintenance job.
- **`src/api/worker.py` (`python -m api.worker`) — the worker machine entrypoint.** Same image as
  the API, different CMD. **Deliberate deviation, logged per the continuity rules:** it is a
  *recovery-sweep + maintenance* machine (periodic `lease.sweep_expired` crash-recovery sweep,
  a `run_maintenance` pass twice a day, a structured `worker.health` log line), **not a task
  runner** — runs still execute inside the API process (`run_pipeline` background task, the
  single-worker design Phase 02/12 accepted). The phase doc's "a heavy run cannot starve the API"
  premise is therefore not yet true; handing task execution over to the worker is real Phase 02/10
  architecture work (the executor's leasing already supports it) and is logged here rather than
  silently implied otherwise.
- **Migration `0012_run_stats` + `GET /metrics` (authenticated).** `run_stats` makes the extraction
  drop-rate durable — `api.cli.record_run_stats` is called at run-finish by both `cli.run_query` and
  `web.runner.run_pipeline` (the in-memory `RunStats` was never persisted before). The metrics
  endpoint (`src/api/web/routes/metrics.py`, registered in `app.create_app`, requires a Supabase
  JWT) reports the runbook's nine-alert values with threshold constants colocated: runs/day,
  cost-per-run 30d mean, search spend MTD, **sentence binding rate (the only `< 100%` page-
  immediately alert — breaches below, not above, its threshold)**, extraction drop rate (from
  `run_stats`, first time the drop metrics are queryable anywhere), p95 run latency, task failure
  rate, and DB size as % of 500 MB (70%/85% levels). Covered by `tests/integration/test_metrics.py`
  (3 tests; shared-DB-safe bounds per the long-lived `ai_pi_test` gotcha).
- **Verification.** `make check` green: **819 passed, 16 deselected, 96% coverage** (up from 806 /
  96.06%; `api.maintenance` measured at 25% via `--cov=api.maintenance`, `metrics.py` 99% through
  the `src/api/web` prefix). Docker build succeeds (pinned digests `ghcr.io/astral-sh/uv:python3.12-
  bookworm-slim` + `python:3.12-slim`; runs as uid 999 `app`; `api.web.main`/`api.worker`/
  `api.maintenance` import cleanly in the container). `deploy/backup.sh` `bash -n` clean; both
  workflow YAMLs parse; `fly config validate` passes for both `fly.toml` and `fly.worker.toml`
  (flyctl v0.4.81).
- **Supabase pre-deploy migration check.** The full chain 0001→0012 applied cleanly to the real
  Supabase project through its IPv4 session pooler (the direct endpoint is IPv6-only here) and
  against a real-shaped `auth` schema. The 0001 `auth`-schema-exists guard skipped the local stub,
  and `user_profiles_user_id_fkey` resolves `user_profiles -> auth.users`.
- **Live operations completed 2026-08-12:** real Supabase migration; Fly API health check and worker
  heartbeat; deploy workflow (`make check` → image → migration → worker → API → health); kill-switch
  trip/reset against production; keepalive and maintenance; and a PG17 `pg_dump` uploaded to R2
  (`ai-pi-postgres-2026-08-12T064705Z.sql.gz`). The backup workflow pins its public account endpoint
  and bucket in version control and maps the R2 token to the AWS CLI variable names. Remaining:
  OAuth login/linking with both providers, one authenticated run with SSE through Fly, an eviction
  drill-down against real report data, the first authenticated `/metrics` read, and the monthly
  scratch-database restore. Vercel correctly shows its static fallback because production has zero
  published benchmark reports; run/publish benchmarks, then redeploy Vercel to bake them into HTML.

### 2026-08-11 — Reddit removed as a source (D5 closed as "dropped")

- **Reddit is no longer a source.** The manual 2–4 week app-approval process made it infeasible,
  so `api.sources.reddit.py`, the `ENABLE_REDDIT`/`reddit_client_id`/`reddit_client_secret`
  settings, the `reddit` venue in `mine_community`, the `HandlerDeps.reddit` wiring, and
  `tests/integration/test_reddit_flag.py` are all deleted. The untracked `product-investigator/`
  Devvit app directory was deleted too (an official Reddit app — nothing to do with the ai-pi
  repo). All remaining mentions of Reddit are decision traces: the masterplan's initial plan and
  the D5/phase-doc/tracker records of why it was skipped. Search hits that point at a reddit.com
  page still work as ordinary page content; `reddit.com` stays in `NON_CANDIDATE_DOMAINS` so a
  search hit can never become a competitor entity. `make check`: 573 passed, 0 failed (coverage
  gate not measured here — no Postgres/Docker in this environment, 237 integration tests skipped).

### 2026-08-10 — Phase 14 follow-up: owning-phase fix set lands, re-measured, committed
- **Landed the previously-uncommitted fix set** (the 8-file dirty diff that contradicted the
  committed docs' "not fixed here" claims): `mode="auto"` + `DISCOVERY_SEARCH_LIMIT=20`
  (cli.py, discover.py), `ValueKind.LIST` contradiction skip (contradictions.py), `pricing.model`
  gains `free` (claims.py, extract_claims.md), `build_competitors` free-path (assemble.py),
  `consider_oss` prompt rewrite (plan_dag.md), ReportView "Free" (web).
- **Added the awesome-* filter** (`discover.py` `_is_github_list_repo`; `_seed_awesome_repos` →
  `_seed_github_repos`, query `"<cat> in:name,description stars:>100"`) — a curated list is not a
  product; general search is now unambiguously the priority channel. Unit-tested (6 new tests).
- **06b implemented**: Exa snippet → grade-C synthetic source (`serp_snippet`, `#serp-snippet`
  canonical URL, `pricing.*` dropped). Integration-tested (`test_snippet_claims_persist_grade_c...`,
  `test_empty_snippet_is_a_no_op`).
- **Tests for every in-diff behavior** (contracts, synth free-path, contradictions LIST, exa
  payload auto-mode, discover limit/filter, ReportView vitest). Fixed a pre-existing
  closed-client bug in `test_api.py`'s finding-drilldown test (the Phase 13 no-live-Postgres gap).
- **Live re-measurement** (q01/q04/q08, $0.37): discovery improved (q01 0→4 real competitors,
  q04 expensify.com surfaced, q08 real OSS), **recall still 0.00** — located a second bottleneck:
  `value_type_mismatch`=54 on q01 (extractor emits prose for boolean/numeric), profile timeout on
  JS pricing pages, and a cross-scheme scoring gap (`gh:` vs `web:` ground truth, q08 mkdocs).
- **`merge_alias` FK fix** (repoint claims before delete) — q08's discovery crashed on
  `claims_entity_id_fkey` live pre-fix; re-ran clean. Integration test added.
- **06a drop-trace**: replayed 257 cached extractions (987 raw claims, 157 drops) — verdict: no
  quote-length floor; causes are entity-decoding mismatch + prompt/schema compliance.
- **Docs reconciled**: benchmark.md/tuning.md "not fixed here" claims amended with the outcome +
  full follow-up sections; tracker Current Status/Focus updated. `make check` 806 tests, 96.06%.
- Decisions 06b–11 closed per user walkthrough; Reddit dropped as a source (manual app-approval
  process made it infeasible — see Key Decisions D5).

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
- **Implemented Phase 00**: repo scaffold (`pyproject.toml`, `Makefile`, `docker-compose.yml`,
  `.env.example`, `alembic.ini`), `src/api/{config,db,logging}.py`, all six model modules
  (`claims`, `entity`, `plan`, `events`, `brief`, `report`), migration `0001_initial` (full
  masterplan §4.3 schema plus the three Phase 00 deltas), unit + integration test suites, and
  `.github/workflows/ci.yml`. `ruff check`, `ruff format --check`, `mypy --strict`, and the unit
  test tier (44 tests, 96.78% coverage on `src/api/models/`) all pass locally.
- **Resolved the Phase 00 "asyncpg vs SQLAlchemy Core" open decision**: asyncpg + hand-written
  SQL, per the phase doc's own lean. Alembic itself still needs a synchronous SQLAlchemy engine
  to run migrations, so `psycopg[binary]` + `sqlalchemy` were added as a migration-only
  dependency; the app runtime path stays asyncpg-only.
- Integration tests (`alembic upgrade/downgrade`, CHECK/UNIQUE constraint tests) are written and
  skip gracefully (not fail) when no Postgres is reachable — verified locally as 4 skips before
  Docker was available.
- **Ran full DB-backed verification** once Docker Desktop's WSL integration was enabled: `make
  db-up`, created the `ai_pi_test` database (not yet auto-provisioned — see `working_knowledge.md`
  Known Issues), ran `make check` against live Postgres. Found and fixed two real bugs in the
  process:
  1. **Schema bug** — `findings_must_cite` used `array_length(claim_ids, 1) >= 1`. Postgres
     returns `NULL` for `array_length` on an empty array, and a `CHECK` constraint treats `NULL`
     as passing, so empty `claim_ids` were silently allowed — the exact case the constraint exists
     to prevent. Fixed with `cardinality(claim_ids) >= 1`, which correctly returns `0` for empty
     arrays. This is a genuine breaking fix to a Phase 00 contract (schema DDL), logged per the
     phase doc's own rule that breaking changes here need a tracker note explaining what was
     learned: **`array_length` is not a safe emptiness check in Postgres; use `cardinality`.**
  2. **Test bug** — integration test helpers used `id(conn)` to generate "unique" fixture values
     (emails, URLs) across test functions; CPython reuses object ids after GC, so two tests
     collided on a real unique constraint. Fixed with `uuid.uuid4()`.
  All 51 tests now pass (47 unit + integration, 0 skipped) against live Postgres, and `make check`
  is fully green — no more DB-backed unknowns.
- Wrote `docs/working_knowledge.md` (architecture, conventions, workflows, known gotchas) and
  closed out this Phase 00 entry.
- **Implemented Phase 01**: `spikes/` scaffolded (shared VCR + secret-scrubbing helpers in
  `spikes/_common.py`), `vcrpy`/`httpx`/`trafilatura`/`playwright` added to `pyproject.toml`
  (`httpx`/`vcrpy` in `dev` since `tests/integration/test_fixture_corpus.py` needs them
  permanently; `trafilatura`/`playwright` in a spikes-only extra). A `TID251` ruff rule bans
  `import spikes` from anywhere else, per the phase doc's exit criterion.
  - **Credential-free vendors** (HN Algolia, Wayback CDX, npm/PyPI, Stack Exchange): all GO. Found
    a real deviation from the phase doc's own assumption — **Stack Exchange's quota is reported in
    the JSON response body (`quota_max`/`quota_remaining`), never in headers.**
  - **Crawl viability** (40 real pricing pages, see `spikes/pricing_corpus.py`): static
    httpx+trafilatura hit rate **88%** (35/40), path-guess hit rate **82%** (33/40, both misses
    genuine multi-product enterprise vendors), Playwright recovered only **1/5** static failures
    (3 of the other 4 are Cloudflare anti-bot walls Playwright doesn't get past either).
    **Decision: Playwright deferred behind a feature flag, not built — masterplan §14 open item #3
    is closed.** Found and fixed a real bug along the way: the price-detection regex only matched
    `$`, and `hubspot.com` was serving India-localized ₹ pricing to this environment's egress IP —
    broadened to match `[$€£¥₹]` + currency codes.
  - **GitHub** (fine-grained PAT): rate limits confirmed — 5,000/hr general, but **search API is
    capped separately at 30/min**, much stricter, and the masterplan's reactions-sorted issue
    query pattern must budget against that number. **Found a real blocker**: the Starring endpoint
    (needed for 90-day star velocity) returns 403 on both REST and GraphQL with a fine-grained
    PAT's default "Public repositories (read-only)" access, even though star data is public. Needs
    a classic PAT or an explicit Starring permission before Phase 04/07 build on this signal.
  - **Exa**: GO. Recall solid (3/4 mainstream, 3/4 mid, thin queries returned genuinely relevant
    named competitors e.g. FloCRM for "WhatsApp first CRM for Indian SMBs"). Cost is a flat
    $0.007/query across neural/keyword/auto modes; `summary` content retrieval is a separate
    +$0.01/query add-on. Derived **~179 runs/month** ceiling against the $10/mo allowance at
    ~8 queries/run — comfortable headroom. No rate limiting observed at a 10-request burst.
  - **OpenRouter / `deepseek/deepseek-v4-flash`**: GO, with a usage rule. Structured-output
    violation rate is 0/50 with and without `provider.require_parameters: true` — clean either
    way on this model. Prompt caching **works but is unreliable**: only 1/10 identical-prefix
    calls hit the cache (OpenRouter's default routing isn't sticky to one backend node) — a real
    deviation from the masterplan's assumed consistent ~4× saving, though the saving matches the
    pricing ratio exactly when it does land. Measured 60-page run cost: **$0.012**, well under the
    masterplan's $0.03 estimate. **Found an important latency finding**: free-form JSON extraction
    without `response_format` has wildly variable, unbounded output (up to 6,266 completion tokens
    for one call) and tail latency (p95 **62.1s**); with strict schema enforcement, both are tightly
    bounded (p95 **14.7s**, output capped to what the schema needs). **Actionable for Phase 05/06:
    always enforce `response_format`, never free-form JSON** — and even at p50 ≈7s/call, a 60-page
    run must parallelize extraction, not run it serially, to fit the three-minute budget.
  - **Product Hunt**: not started this pass — requires manual developer-app registration, deferred
    as not on the critical path.
  - **Reddit**: **dropped as a source** — its manual 2–4 wk app-approval process made it
    infeasible. Community mining ships on HN Algolia + GitHub + Stack Exchange (D5); the
    codebase carries no Reddit integration.
  - Fixture corpus committed: `tests/fixtures/cassettes/*.yaml` (7 vendors, all secret-scrubbed —
    verified by a new permanent test suite, `tests/integration/test_fixture_corpus.py`, 21 tests)
    and `tests/fixtures/pages/*.html` + `manifest.json` (40 real pricing pages, ~30MB, kept
    unmodified since the point is validating extraction against real pages). Nightly live-drift
    checks added at `tests/live/test_vendors.py`.
  - `spikes/` kept in the repo rather than deleted — "archived" per the phase doc's own either/or
    phrasing, since the scripts are the reproducible evidence behind every number in
    `docs/external_apis.md` and the doc says to re-verify before deployment.
  - Full numbers, verdicts, and rate-limit tables: [`docs/external_apis.md`](external_apis.md).

- **Implemented Phase 02**: `src/api/executor/` — `protocol.py` (`TaskSpec`/`ExecutionPlan`,
  `TaskContext`, `HandlerResult`/`SpawnRequest`, `TaskHandler` protocol, `HandlerRegistry`,
  `ExecutorEvent` union), `store.py` (task/event persistence), `lease.py` (claim/renew/complete/
  fail/skip/sweep, both idempotency guards), `budget.py` (`BudgetTracker`), `retry.py` (retryable
  classification + full-jitter backoff), `core.py` (`Executor.submit(run_id, plan, ...) ->
  AsyncIterator[ExecutorEvent]` — the only public entry point, exactly per the phase doc's exit
  criterion). Migration `0002_executor_core` adds `tasks.node_key`/`depends_on`/`budget_weight`
  and a `run_events` table for cursor-based replay. `make check` green, 90%+ coverage on every
  executor module (94–100%), zero Redis/arq/Celery in dependencies.
  - **Two deliberate, tracker-worthy design decisions**, both surfaced to the user before writing
    code rather than assumed:
    1. **The executor's types are fully decoupled from the Phase 00 domain contracts.**
       `api.models.plan.Plan`/`PlanNode` hard-code the closed `TaskKind` enum (7 real domain task
       kinds), and `api.models.events.RunEvent` hard-codes `kind: TaskKind` too — neither can
       represent synthetic test kinds like `sleep_task` or `spawn_task` without polluting frozen
       Phase 00 vocabulary. `src/api/executor/` therefore defines its own `TaskSpec`/
       `ExecutionPlan`/`ExecutorEvent` with `kind: str`, and has zero imports from `api.models.*`
       anywhere in the package — a literal reading of "knows nothing about products, claims, or
       the web." Phase 10 adapts real `Plan`s into this generic shape at the boundary.
    2. **Budget enforcement is in-memory, per-`Executor.submit()`-call, not cross-worker via
       Postgres.** Matches the phase doc's own reference pseudocode (a plain Python counter inside
       one dispatch loop) and its "Budget arithmetic" unit-test spec exactly. Correct for the
       default single-worker deployment (the phase doc's own Open Decision #1). Known,
       accepted gap: if a second worker process is ever added against the *same run*, each has its
       own tracker and the weight cap is enforced per-process, not globally. Promote to a
       Postgres-backed cross-worker cap only when a real multi-worker deployment needs it.
  - **One non-obvious schema choice**: `tasks.lease_expires_at` does double duty as "earliest
    retry time" while `pending` (set by a retryable failure) as well as its usual "lease deadline"
    meaning while `running` — avoids a second column for what is, in both cases, "don't touch this
    row before this timestamp."
  - **A genuine, reproducible concurrency bug found and fixed during hardening**: the first
    dead-branch-detection design ("claim_next found nothing + nothing running + pending>0 ⇒ dead
    branch") raced against retry backoff — a task waiting out a very short jittered backoff (full
    jitter can land near zero) could have its backoff elapse in the gap between the dispatch
    loop's claim attempt and its dead-branch check, get misclassified as unreachable, and be
    skipped instead of retried. Reproduced reliably under `pytest-repeat` (flaked ~1 run in 6 on
    the tightest-timing chaos test) despite passing cleanly on every single unrepeated run — the
    exact failure mode the phase doc warns "concurrency bugs are probabilistic; a single green run
    is weak evidence" about. Fixed by making dead-branch detection depend only on dependency state
    (`lease.skip_unreachable` now matches a `pending` task only if one of its `depends_on` names a
    task that is `failed`/`skipped`/missing — never on timing), which is both simpler and
    provably race-free. Re-verified at 30x repeat, zero flakes, ~660/660 passed. **Actionable
    finding for future phases: never gate a terminal-state decision on "nothing happened in a time
    window" when a precise, state-based predicate is available instead — timing-based heuristics
    for irreversible transitions are a recurring race-condition source.**
  - Nightly CI (`.github/workflows/nightly.yml`, new) runs the lease/executor/chaos suite 50x via
    `pytest-repeat`, per the phase doc's exit criterion; `ci.yml` gained a `make migrate` step
    ahead of `make check` since the new integration tests assume the schema exists rather than
    self-migrating (only `test_migrations.py` did that before).
  - Full design/scope: [`docs/execution_phases/phase-02-executor-core.md`](execution_phases/phase-02-executor-core.md).

- **Implemented Phase 03**: `src/api/retrieval/` — `canonical.py` (`canonicalize_url`, 8-rule
  canonicalisation, idempotence proven by a structured-URL Hypothesis strategy since fuzzing raw
  strings mostly produces invalid URLs that don't exercise the rules), `robots.py` (`RobotsCache`,
  24h TTL, hardcoded G2/Capterra no-crawl set enforced independently of robots.txt),
  `extract_text.py` (trafilatura wrapper + the single canonical `normalise()` pass, idempotence
  proven the same way), `pathguess.py` (`PRICING_PATHS`/`DOCS_PATHS`/`CHANGELOG_PATHS`,
  `PRICE_TOKEN_RE`, `guess_path` orchestrator), `fetch.py` (`HostThrottle`, retry loop reusing
  Phase 02's `api.executor.retry` unmodified, conditional requests, streamed size cap, the
  `fetch_source(url) -> Source` entry point), `cache.py` (source cache + path-guess cache
  read/write), `errors.py` (typed failure hierarchy). New model: `api.models.source.Source`.
  Migration `0003_fetch_source_cache` adds `sources.etag`/`sources.last_modified` and two new
  tables, `path_guess_cache` (positive/negative resolution per root domain + path kind) and
  `path_guess_attempts` (append-only instrumentation log — the raw material for the hit-rate
  measurement). `trafilatura` and `httpx` promoted from Phase 01's spike-only/dev-only deps to
  core `src/api` dependencies now that `api.retrieval` uses them for real; `playwright` stays
  spikes-only (still deferred, per Phase 01's decision). `make check` green: 147 tests (up from
  ~104), 95.8% coverage overall, every `retrieval/` module individually ≥ 90%. Coverage scope and
  the pytest `--cov` flags extended to include `src/api/retrieval`.
  - **HTTP integration tests use `httpx.MockTransport`, not VCR cassettes** — a deliberate
    deviation from Phase 01's cassette convention, but not an arbitrary one: the phase doc's own
    test spec for the cache-hit-makes-zero-network-calls case says "asserted by a mock transport
    that fails on any request," which only makes sense for `httpx.MockTransport`. VCR cassettes
    exist to replay *real vendor* traffic (Exa, GitHub, ...); this layer's own HTTP mechanics
    (retries, redirects, conditional requests, size caps) have no vendor to record against, so a
    scripted transport (`tests/integration/_http.py`) is the more direct fit and needs no cassette
    files. The Phase 01 fixture corpus (`tests/fixtures/pages/`, `manifest.json`) is still reused
    as-is for extraction-quality testing rather than duplicated.
  - **Two deliberate design decisions surfaced before writing code:**
    1. **Path-guessing gets its own cache table, not a general reuse of the source cache's TTL.**
       The phase doc asks for positive (7d) vs negative (24h) TTL specifically on the
       *path-resolution* outcome ("does this domain have a `/pricing`"), which is a different
       question from "is this specific URL's content still fresh" — conflating the two would mean
       a 404 on `/pricing` (already negatively cached as a `Source` row) and "no pricing page
       exists for this domain" caching would need to derive one from the other implicitly.
       `path_guess_cache`/`path_guess_attempts` make both the resolution and the instrumentation
       explicit and independently queryable (`cache.path_guess_hit_rate()` is a real SQL
       aggregate, not a derived guess).
    2. **Canonicalisation's rule 2 (force https where the host actually redirects to it) is split
       across two places.** `canonicalize_url()` itself never upgrades scheme without evidence
       (it's a pure function with no network access, so it can't observe a redirect) — the upgrade
       happens in `fetch.fetch_source` after `_fetch_with_retries` returns, by comparing the
       post-redirect `final_url`'s scheme against what was requested. This matches the phase doc's
       own framing ("record the observed redirect rather than assuming") and keeps
       `canonicalize_url` itself pure and unit-testable in isolation.
  - **A genuine bug found and fixed while writing the canonicalisation property test**: the first
    percent-encoding pass did `quote(unquote(component), safe=...)`, i.e. decode everything then
    selectively re-encode. That's wrong for *reserved* characters — `%2F` (an encoded literal slash
    inside one path segment) decoded to a real `/` and was never re-encoded, silently changing
    `foo%2Fbar` (one segment) into `foo/bar` (two segments), a real change to what the URL means,
    not just its spelling. Caught by `test_canonicalisation_table`'s `%2f` case before it reached
    the property test. Fixed by only decoding `%XX` sequences that spell an RFC 3986 *unreserved*
    character, leaving every reserved-character escape (including `%2F`) alone but still
    uppercasing its hex — see `canonical._renormalise_percent_encoding`'s docstring.
  - **Real path-guessing hit rate measured at 75% (30/40)**, run for real via
    `spikes/pathguess_hitrate.py` against `api.retrieval.guess_path` — below both Phase 01's 82%
    and the masterplan's 80% bar, but not a crawl-mechanics regression: `guess_path` matches its
    price-token regex only against the extracted, *normalised* text (as it must, since that's the
    same text span binding will bind against in Phase 06), where Phase 01's spike counted a hit if
    the regex matched *either* the extracted text *or* the raw HTML body. Root-caused per-domain
    (JS-rendered SPA shells with zero server-rendered price content, a genuine gap in the
    price-token regex for percentage/cents-based pricing like Stripe's, and one vendor's pricing
    page that has since become a redirect notice) — full breakdown in `docs/external_apis.md`.
    **Not treated as a reason to loosen `favor_precision=True` or the price regex**: both are the
    phase doc's own specified design, and gaming the extraction settings to hit a rounder number
    would be measuring the wrong thing. Consequence quantified rather than left as a worry: at
    75%, roughly 1 in 4 pricing lookups falls through to a real Exa query instead of a direct
    fetch — still comfortably inside the ~179 runs/month ceiling from Phase 01's search bake-off.
    Live regression guard added at `tests/live/test_pathguess_hitrate.py` (threshold 65%, with
    slack below the measured 75% for day-to-day vendor content churn without being flaky).
  - Full design/scope: [`docs/execution_phases/phase-03-fetch-source-cache.md`](execution_phases/phase-03-fetch-source-cache.md).

### 2026-08-07

- **Implemented Phase 04**: `src/api/search/` (Exa behind a `SearchProvider` protocol, credit-
  ledger allowance tracking, 24h result cache, per-run `RetrievalBudget`) and `src/api/sources/`
  (GitHub, HN Algolia, Wayback CDX, npm/PyPI, Stack Exchange, Product Hunt, SERP-snippets).
  Migration `0004_search_domain_retrievers` adds `search_cache` and `search_credit_usage`
  (append-only ledger, dollars not query counts). `config.py` gains `producthunt_token` and
  `exa_daily_credit_cap_usd`/
  `exa_global_daily_credit_cap_usd` — all `None`/`False` by default, TBD until
  [Phase 14](execution_phases/phase-14-benchmark-calibration.md), same pattern as every other
  quota knob. `make check` green: 205 tests (up from 147), 95.95% coverage overall.
  - **Two "budget" concepts, kept deliberately separate.** `api.search.budget.RetrievalBudget`
    (modeled directly on Phase 02's `BudgetTracker`) is an in-memory per-run *call-count* cap —
    `spend_search()`/`spend_fetch()` raise `BudgetExhaustedError` synchronously, for a future
    Phase 10 task handler to catch. The Exa *credit* allowance ($10/mo) is a different thing
    entirely: a persisted, system-wide ledger (`search_credit_usage`), enforced inside
    `api.search.router.SearchRouter.search()`. Exhaustion there does **not** raise — the router
    catches its own `AllowanceExhaustedError` (and any `ProviderError`) internally and returns a
    `SearchResponse(degraded=True, ...)`, per the phase doc's explicit "degradation is a designed
    path, not an error path". `RetrievalBudget.spend_fetch()` is built and unit-tested but not yet
    wired into `api.retrieval.fetch_source` calls — that wiring is Phase 10's job, out of this
    phase's scope by the phase doc's own "Out" list.
  - **"Daily" and "global daily" (the phase doc's two allowance tiers) collapse to one check.**
    `search_credit_usage` is already system-wide, not per-run/per-user, so a single rolling-24h
    `SUM` against `settings.exa_daily_credit_cap_usd` covers both; `GLOBAL_RUNS_PER_DAY` stays a
    derived reporting quantity rather than a separately-enforced counter until real per-user quotas
    exist ([Phase 12](execution_phases/phase-12-api-auth-quotas.md)). Both cap settings default to
    `None` (unenforced) until [Phase 14](execution_phases/phase-14-benchmark-calibration.md)
    measures real credits/run.
  - **`Retriever` is a two-field marker protocol** (`name`, `grade`), not one uniform method — the
    phase doc's own table shows repo metadata, download counts, and search snippets as
    fundamentally different shapes, so each module (`api.sources.github`, `.hn`, `.wayback`,
    `.packages`, `.stackexchange`, `.producthunt`, `.serp_snippets`) exposes its own
    typed async methods and colocated Pydantic record models instead.
  - **GitHub's Starring-endpoint 403 (open since Phase 01) is now proven, not just documented.**
    `GitHubRetriever.star_velocity_90d` still calls the real endpoint (no workaround, since none
    exists short of a credential upgrade this phase can't perform) and converts a 403 into
    `RetrieverUnavailableError` — a coverage gap, not a crash — verified against the **real
    recorded 403** in `tests/fixtures/cassettes/github_api.yaml`
    (`tests/integration/test_github_retriever.py::test_star_velocity_degrades_on_the_real_recorded_403`).
    Star velocity stays a genuine coverage gap in every run until the PAT is upgraded (see Next
    Steps — unchanged from Phase 01/03's carried-forward item).
  - **SERP-snippet reading for G2/Capterra is structurally, not just conventionally, safe.**
    `api.sources.serp_snippets` imports only `api.search` types — no `httpx`, no
    `api.retrieval.fetch` anywhere in the module — so there is no code path that could ever issue a
    direct fetch to an aggregator domain. Proven two ways: a fake-provider integration test, and an
    AST-based test asserting the module's import list contains neither (a plain substring check
    misfired on the module's own docstring, which names both in explaining why it doesn't import
    them — fixed before it shipped). Reuses `api.retrieval.robots.NO_CRAWL_DOMAINS` as the
    domain allowlist rather than redeclaring it, so there is exactly one no-crawl source of truth.
  - **Cassette reuse, not re-recording**, for six of the eight retrievers/providers: GitHub, HN
    Algolia, Wayback CDX, npm/PyPI, Stack Exchange, and Exa all replay Phase 01's committed
    cassettes (`tests/fixtures/cassettes/*.yaml`) using the spikes' own literal inputs (repo
    `microsoft/vscode`, query `"linear alternative"`, domain `stripe.com`, packages
    `react`/`requests`, query `"project management tool alternative"`). New helper
    `tests/integration/_vcr.py` mirrors `spikes/_common.py`'s secret-scrubbing `vcr.VCR` factory
    rather than importing it — `TID251` bans `import spikes` outside `spikes/` itself, and that
    lint runs over `tests/` too. Exa is POST-with-a-JSON-body, where VCR's default matcher ignores
    body entirely; `ExaProvider`'s default request shape (`mode="neural"`,
    `include_contents=True`) was built to reproduce interaction #37 of `search_exa.yaml` byte-for-
    byte (verified by inspection, then by the test itself passing with `body` added to
    `match_on`), which is also the one recorded call with real page `text` to build a `snippet`
    from. Product Hunt (developer token still pending) and the SERP-snippets path (no site-scoped
    Exa call was ever recorded) have no cassette and are `httpx.MockTransport`-tested instead —
    called out explicitly in both the code and here rather than silently implied otherwise.
  - **A real, repeatable test-isolation bug found and fixed while writing the integration suite**:
    the search cache and credit ledger are deliberately shared/persistent — no `run_id` in
    `search_cache`'s key (masterplan §9: a second query in an already-explored category should be
    nearly free *across users*), and `search_credit_usage` is a real append-only table with no
    per-test cleanup. Tests that used a literal query string or provider name (`"widget makers"`,
    `"credit-ledger-a"`) passed in isolation but **failed when the same test file was invoked
    twice in a row** against this project's long-lived local Postgres container — the second
    invocation's "first ever call" was silently served from a stale cache row (or a ledger sum was
    silently doubled) left by the first invocation, producing assertion failures that looked like
    real router/ledger bugs but were purely test-fixture contamination. Fixed by generating a
    fresh, `uuid4`-suffixed query/provider name per test call — the same reason
    `tests/integration/_http.py` already has its own `unique_root()` for the Phase 03 source cache.
    **Actionable finding for future phases: any test against a cache or ledger keyed independently
    of a fresh per-test id (like `run_id`) needs a uniqueness strategy of its own, or it will pass
    once and fail on repeat** — re-run any new integration test file twice in a row before trusting
    a single green run, the same spirit as Phase 02's "a single green run is weak evidence" for
    concurrency bugs.
  - Two modules sit at the coverage floor rather than near-100%, both from defensive branches that
    are real but not independently exercisable without a second real cassette: `exa.py` (85%) —
    the retry/backoff loop's non-final-attempt branches, already proven correct by the identical,
    shared `api.executor.retry` code path Phase 02/03 hardened; `github.py` (93%) — a couple of
    `_contributors_count` edge branches. Not treated as a gap worth chasing with contrived mocks;
    flagged here instead of silently accepted.
  - Full design/scope: [`docs/execution_phases/phase-04-search-domain-retrievers.md`](execution_phases/phase-04-search-domain-retrievers.md).

- **Implemented Phase 05**: `src/api/llm/` — `client.py` (OpenRouter transport, Phase 02 retry
  policy reused unmodified, provider pinning + `temperature: 0` unconditional), `prompts.py`
  (`PromptRegistry` — YAML-frontmatter + `## section`-body `*.md` files, cache-optimal assembly,
  structural untrusted-content delimiting), `cost.py` (`MODEL_RATES` config table + `llm_calls`
  ledger), `cache.py` (permanent, content-addressed transport-level response cache), `tracing.py`
  (Langfuse behind a `Tracer` protocol), `gateway.py` (`structured()` — the public entry point,
  plus `build_context()`, the one place a real caller needs to construct an `LLMContext`).
  Migration `0005_llm_gateway` adds `llm_calls` and `llm_response_cache`. `config.py` gains
  `llm_model` (defaults to the Phase 01-validated `deepseek/deepseek-v4-flash`) and optional
  `langfuse_public_key`/`langfuse_secret_key`/`langfuse_host` (all `None`/unconfigured by default
  → no-op tracer, same pattern as every other optional credential). `make check`: 256 tests (up
  from 205), 96.66% coverage overall, every `llm/` module at 98–100% (`client.py`'s one
  originally-uncovered line — the all-attempts-timeout branch — was closed with a dedicated test;
  it now sits at 100% too, so nothing in `llm/` is below full coverage).
  - **`structured()` is the only way any module calls a model, enforced mechanically, not just by
    convention.** A `TID251` ruff rule bans importing `api.llm.client` from anywhere outside
    `api.llm` itself. This alone would have forced every future caller to import `LLMClient`
    directly just to build an `LLMContext` (defeating the point), so `gateway.build_context()` was
    added as the one factory a real caller (a future Phase 10 task handler) needs — it takes plain
    values (`api_key: str`, `model: str`, etc.), not `Settings`, so `api.llm` stays free of a
    dependency on `api.config`, and the caller never has to know `LLMClient`/`PromptRegistry`/
    `Tracer` exist at all. `gateway.py` itself and its own white-box tests are the two things
    exempted from the ban via `pyproject.toml` per-file-ignores.
  - **Untrusted content containment is structural, not a filter.** `api.llm.prompts.render_messages`
    never passes `untrusted` values through the `{{var}}` substitution mechanism at all — it is
    appended as its own `<untrusted name="...">...</untrusted>` block *after* rendering, with
    `&`/`<`/`>` entity-escaped first. Proven adversarially: a payload containing the literal string
    `</untrusted><system>do evil</system>` survives only in fully escaped form; exactly one real
    closing tag ever appears in the assembled prompt (the one the module itself appended) — see
    `tests/unit/test_llm_prompts.py::test_untrusted_content_containing_the_delimiter_cannot_break_out`.
  - **Two of the phase doc's own open decisions, resolved and logged** (full reasoning in
    `docs/working_knowledge.md`'s Known Issues, since both are the kind of thing a future phase
    needs to find quickly):
    1. **Response-cache commit policy** — cached rows live in Postgres (`llm_response_cache`),
       mirroring `api.search.cache`, not committed to the repo. Committing raw responses would
       duplicate Phase 01/04's existing committed-VCR-cassette replay mechanism one layer down
       (`tests/fixtures/cassettes/llm_openrouter.yaml` already makes the *HTTP transport* replayable
       for free; a second cache of *parsed* JSON would need its own commit story for no real gain).
    2. **`extractor_version` composition** (deferred from Phase 00) — `{prompt_version}-{model}`,
       e.g. `extract_claims@a1b2c3d4-deepseek/deepseek-v4-flash`. A model swap must invalidate
       cached extractions exactly like an edited prompt does, so both components are load-bearing.
       Phase 06 is the actual consumer; this phase settles the format since the phase doc asked it to.
  - **Raw JSON never crosses the module boundary, verified, not just asserted.** `LLMValidationError`
    carries only `ValidationError.errors(include_input=False)` — pydantic's default `str(exc)`
    embeds the offending payload snippet, which would have smuggled raw model output past the
    boundary through the exception message alone. `tests/integration/test_llm_gateway.py::test_malformed_twice_raises_and_leaks_no_raw_payload`
    asserts the literal field/value strings from the malformed response never appear in the raised
    error.
  - **A real, repeatable test-isolation bug found and fixed while writing the integration suite** —
    the same shape as Phase 04's own lesson, but hit fresh: `llm_response_cache` has no `run_id` in
    its key by design (masterplan §9 — a repeated call should be nearly free), so the first draft of
    `tests/integration/test_llm_gateway.py` used a literal `{"message": "hi"}` variable across
    *multiple test functions in the same file* and one test's cached response silently answered a
    later, unrelated test's "first" call. Fixed by giving every call a `uuid4`-suffixed `message`;
    re-run twice in a row to confirm — see `docs/working_knowledge.md`'s Known Issues, now with two
    independent phases' worth of the same lesson on record.
  - **A second, genuinely new test-methodology bug found while running the live checks**: invoking
    `make check` (which runs `test_migrations.py`'s `downgrade base`/`upgrade head` cycle) against
    the same shared `ai_pi_test` database while a ~4-minute live gateway test was still in flight
    dropped and recreated `runs` mid-test, deleting a row the live test had already inserted and
    surfacing as a `ForeignKeyViolationError` on `llm_calls.run_id` — a stack trace that looked
    exactly like a real gateway bug but was pure test-scheduling interference. Re-running the live
    test alone afterward (nothing else touching Postgres concurrently) passed clean. New
    `docs/working_knowledge.md` entry: never run a schema-modifying test alongside any other
    long-running test against the same database.
  - **Live checks (real OpenRouter traffic through the real `structured()` gateway, not raw HTTP)
    — both from the phase doc's own "Live (nightly)" test row, run once here rather than left
    theoretical:**
    - **Schema violation rate: 0/20 (0%)** — no repair retry needed on any of 20 real
      extraction-shaped calls against the Phase 01 fixture corpus. Matches Phase 01's own 0/50
      baseline with `require_parameters: true` exactly.
    - **Prompt-cache hit rate: 2/10 calls showed `cached_tokens > 0`** (1,536 cached tokens each,
      against a ~7.3k-character static prefix) — better than Phase 01's 1/10, same underlying
      phenomenon (OpenRouter's routing isn't sticky to one backend node, so the vendor's own prompt
      cache lands intermittently, not reliably). Confirms `docs/working_knowledge.md`'s existing
      Known Issues entry rather than changing it — still don't count on this in the cost model
      without further work.
    - Total spend for both live checks (30 real calls): **$0.0033** — `extract_plan` (20 calls):
      $0.00195, 9,775 input + 5,941 output tokens; `cache_probe` (10 calls): $0.00137, 16,702 input
      + 466 output + 3,072 cached tokens. Both comfortably inside the essentially-free LLM budget
      from Phase 01's cost model.
  - Full design/scope: [`docs/execution_phases/phase-05-llm-gateway.md`](execution_phases/phase-05-llm-gateway.md).
- **Implemented Phase 06**: `src/api/extract/` — `span.py` (`bind_span` — the masterplan §4.8
  function verbatim: `str.find`, no fuzzy matching anywhere in the module, an ambiguous quote drops
  rather than resolving to the first occurrence; `quote_context_window` — ±2000 chars, clamped,
  offset-tracked), `metrics.py` (`DropReason` — `quote_not_in_source`/`quote_ambiguous`/
  `invalid_attribute`/`value_type_mismatch` — `DropCounts`, `ExtractionMetrics.drop_rate`),
  `validate.py` (`RawExtractedClaim`/`ExtractionResponse` — the schema `structured()` validates the
  model's JSON against; `ExtractedClaim` — the post-gate type, deliberately missing `entity_id`/
  `run_id`/`grade`/`confidence`, carrying `candidate_entity_hint` instead, since resolution/grading
  are Phase 07/08's scope; `validate_and_bind` — vocabulary → value-type → span, in order, each
  gate its own drop reason), `cache.py` (`content_hash + extractor_version`, permanent, re-binds
  cached claims against the *current* source text on every read rather than trusting stored
  offsets), `extractor.py` (`extract_claims(source, *, ctx) -> ExtractionResult` — one `Source` per
  call, never batched; `extractor_version_for` — `f"{prompt_version}-{model}"`, the format Phase 05
  settled). `src/api/prompts/extract_claims.md` is the first real (non-synthetic) prompt file in
  the repo. Migration `0006_claim_extraction` adds `extraction_cache`. `make check`: 318 tests (up
  from 256), 97.02% coverage overall, every `extract/` module at **100%** (raised bar per the phase
  doc — this is the core guarantee).
  - **`bind_span` reused as-is for the property tests that matter.** Two hypothesis properties, both
    over an unbounded input space rather than hand-picked cases: for random `prefix`/`suffix` built
    from digits and a random `needle` built from disjoint-alphabet letters (guaranteeing exactly one
    occurrence by construction, not by luck), `text[bind(text,needle).start:...end] == needle`
    round-trips; for `needle` drawn from an alphabet that never appears in `text`, `bind_span` always
    returns `None`. `tests/unit/test_span.py` also carries every table-driven case the phase doc
    lists by name (differ-by-one-char, whitespace-only difference, NFC-vs-decomposed Unicode, empty
    quote, quote longer than source, emoji/CJK offset consistency) — all resolve to `None` or a
    correct `Span` as specified, with zero fuzzy-matching code path anywhere to have gotten them
    wrong.
  - **The four drop reasons stay distinguishable without threading a reason through `bind_span`
    itself.** `bind_span` stays exactly the phase doc's spec'd two-branch function (both branches
    return bare `None`); `validate.py`'s private `_span_drop_reason` re-derives which branch fired,
    purely for `DropReason` accounting, only ever called after `bind_span` has already returned
    `None`. Keeps the most-tested function in the module free of a metrics-accounting concern it
    doesn't need to carry.
  - **The extraction cache stores raw, pre-gate claims, not the final `ExtractedClaim`s — the fail-
    safe property this buys is proven, not just asserted.** A test inserts a raw cache row directly
    via `cache.put`, then calls `extract_claims` twice against two different `Source`s sharing that
    same `content_hash` (a stand-in for a Phase 03 normalisation change altering `extracted_text`
    without changing the hash the cache actually keys on, since a real hash change would just be an
    ordinary cache miss): the first call's quote is present in its source and binds; the second
    call's identical raw claim, replayed from cache, misses against the second source's different
    text and surfaces as a fresh `quote_not_in_source` drop rather than a silently-wrong span.
  - **`ExtractedClaim` is a new type, not a partially-filled `api.models.claims.Claim`.** The phase
    doc's own scope list rules out entity resolution and grading here, but `claims.Claim.entity_id`/
    `grade`/`confidence` are all non-optional on the frozen Phase 00 contract — constructing one
    without them isn't possible, nor should it be (the DB row genuinely doesn't exist yet at this
    stage of the pipeline). `ExtractedClaim` (in `api.extract.validate`) mirrors `Claim`'s span/value
    fields exactly but stops there, plus `candidate_entity_hint: str | None`, matching the phase
    doc's "claims carry a candidate entity hint; resolution happens later" line. Phase 07 is
    responsible for turning `ExtractedClaim` + `candidate_entity_hint` into a real `entity_id` and
    constructing the actual `Claim` row (grading is Phase 08 on top of that).
  - **10-fixture adversarial-inclusive corpus** (`tests/fixtures/extraction/`, offline — each fixture
    is `<name>.txt` already-extracted text + `<name>.llm.json` a committed fake model response +
    `<name>.expected.json` the claims/drop-reason-counts that must survive) instead of the phase
    doc's literal `*.html` suggestion: Phase 03 already owns an HTML→text fixture corpus
    (`tests/fixtures/pages/`) for extraction-quality; re-deriving text from HTML here would make
    Phase 06's own corpus depend on trafilatura's output for no benefit, since this phase's contract
    starts one step later, at `Source.extracted_text`. Covers clean pricing, a pricing table, a
    no-extractable-price page, a changelog, a GitHub README, multi-currency (one real $ claim binds,
    one fabricated-conversion claim doesn't), a struck-through discount (real price binds, an
    accidentally-ambiguous `"/month"` quote doesn't), and three adversarial-injection pages. The
    `adversarial_ignore_instructions` fixture reproduces masterplan §8.3's own worked example
    exactly: the injected instruction's best-case outcome is a second, ordinary, correctly-cited
    `pricing.entry_usd_month` claim (value `0`, quote genuinely present because the page's injected
    text contains it) that just contradicts the real one — proven, not just argued, by a dedicated
    test asserting both claims' attributes stay inside the closed vocabulary and neither quote
    contains the injected imperative sentence itself.
  - **A real test-isolation bug found and fixed while writing the integration suite — the third
    phase in a row to hit this exact shape** (Phase 04, then Phase 05, now Phase 06; see those
    entries and `docs/working_knowledge.md`'s Known Issues). `extraction_cache` is keyed on
    `content_hash` alone with no `run_id`, by design (masterplan §9: the same page costs nothing
    forever). The first draft of `tests/integration/test_extractor.py` built `Source.extracted_text`
    from literal strings, so re-running the suite twice against the same long-lived `ai_pi_test`
    container turned the *first* call of the cache-hit and untrusted-content tests into a silent
    cache hit from the *previous run*, making `transport.calls[CHAT_PATH]` read `0` instead of `1`
    and leaving the scripted handler's `captured["body"]` never populated. Fixed the same way as
    Phase 05: `_source()` appends a `uuid4` marker to `extracted_text` (and therefore to
    `content_hash`) unless the caller explicitly wants a shared hash (the re-bind-on-read test wants
    exactly that collision, on purpose). Verified by running the full file twice in direct
    succession before moving on.
  - **Running `alembic upgrade head` locally targets `ai_pi` (from `.env`'s `DATABASE_URL`) by
    default, not the `ai_pi_test` database the test suite actually reads** — not a new discovery
    (`docs/working_knowledge.md` already documents overriding `DATABASE_URL` for a local test-DB
    migration, and CI's `make migrate` step already sets it explicitly), but worth restating here
    since it's an easy first-run trip: `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ai_pi_test
    uv run alembic upgrade head` before running `tests/integration/test_extractor.py`'s Postgres-backed
    tests locally.
  - Full design/scope: [`docs/execution_phases/phase-06-claim-extraction-span-binding.md`](execution_phases/phase-06-claim-extraction-span-binding.md).

- **Implemented Phase 07**: `src/api/resolve/` — `entity_key.py` (PSL-aware `web:` derivation via
  two `tldextract.TLDExtract` instances, one with `include_psl_private_domains=True` and one
  without, compared to derive `is_paas_host`; `gh:` derivation handles bare `owner/repo`, full
  URLs, `git+https://`/`ssh://`/scp-style `git@github.com:` forms, and `.git`/trailing-slash
  stripping; `pypi:` applies real PEP 503 normalisation), `verify.py` (per-scheme artifact
  verification against `fetch_source` for `web:`, `GitHubRetriever.repo_metadata` for `gh:`, direct
  registry calls for `npm:`/`pypi:`, `itunes.apple.com/lookup` for `ios:`, HF's models/spaces API
  for `hf:`, `ProductHuntRetriever.post_by_slug` for `ph:`, and a best-effort Chrome Web Store
  detail-page check with no documented API — all results cached in a new `verification_cache`
  table, 24h TTL), `alias.py` (a pure, DB-free union-find over the three Design-section merge
  triggers — `gh_homepage`, `web_backlink`, `package_repository` — order-independence proven by a
  Hypothesis property test, not just a hand-picked permutation), `maturity.py` (the five-rule
  first-match decision list, with a `known_low_scale` guard so an *unknown* stars/downloads signal
  is never treated as a *known-low* one — that distinction is what keeps `insufficient_signal`
  honest instead of defaulting everything with no data straight to `hobby`), and `store.py`
  (`upsert_entity` — `ON CONFLICT (entity_key)` for the concurrent-same-key case, plus a
  `find_entity_id` pre-check for the "arriving under an already-merged alias" case that a plain
  `ON CONFLICT` can't catch; `merge_alias` — collapses a real duplicate entity into its canonical
  when both sides already exist, or just adds an alias row when only the canonical does, locking
  the two `entity_key`s in sorted order so concurrent merges of the same pair can't deadlock).
  `__init__.py` wires all five into `resolve_entity(ctx, evidence) -> Entity | None`, the phase's
  concrete output. `entities`/`entity_aliases` already existed from Phase 00's `0001_initial`;
  migration `0007` adds only `verification_cache`.
  - **Two small, deliberate extensions to earlier phases**, both additive and covered by this
    phase's own tests: `GitHubRepo` (Phase 04, `api.sources.github`) gained a `homepage` field,
    parsed from a call `verify.py`'s `gh:` check already makes — so the `gh_homepage` alias trigger
    gets its linking fact for free, no extra API call. `ProductHuntRetriever` (Phase 04,
    `api.sources.producthunt`) gained `post_by_slug`, a real GraphQL `post(slug:)` lookup distinct
    from the existing `search_posts` relevance search — `ph:` verification needs "does this exact
    post exist", which relevance search can't answer.
  - **`homepage_url`/`repository_url` are opportunistically observed, not just caller-supplied.**
    `EntityEvidence` carries them as optional caller-known facts (per the phase doc's own framing —
    discovering the underlying facts, e.g. scraping a page's footer for a GitHub backlink, is
    explicitly out of this phase's scope), but `verify_gh`/`verify_npm`/`verify_pypi` also return
    them when the verification call already surfaced the answer, and `resolve_entity` prefers the
    caller's value but falls back to the observed one. `backlink_repo_url` (the `web_backlink`
    trigger) has no equivalent free source — extracting a footer link needs raw HTML, and Phase 03
    only stores `extracted_text`, not raw HTML — so it stays purely caller-supplied; a future phase
    wanting that trigger to fire without help would need to add HTML link-scraping to the fetch
    layer first.
  - **Merging is timing-dependent by design, not just tolerated.** `merge_alias` returns `None`
    (a documented no-op, not an error) when the scheme-precedence-chosen canonical side hasn't been
    independently resolved yet — e.g. a `gh:` candidate's `homepage` points at a domain nothing has
    fetched as its own candidate yet. The merge isn't lost: whichever side arrives second is the one
    that finds the other already resolved, and that direction's own trigger fires and collapses the
    two entities then. Proven end-to-end in `tests/integration/test_resolve_store.py` by resolving
    the same pair in both orders (gh-then-web and web-then-gh) across separate tests, not just
    asserting the pure graph function is order-independent in isolation.
  - **The parked-page minimum-content threshold must stay below `api.retrieval.fetch`'s own
    `THIN_CONTENT_CHARS` (200), not above it.** First draft set `MIN_MEANINGFUL_CHARS = 300`
    (stricter than fetch's own thin-content gate) reasoning it as an independent parked-domain
    signal; that misclassified a genuinely real (if terse) test fixture page — one that legitimately
    cleared fetch's own 200-char gate — as parked, because the *extracted* text landed between 200
    and 300 characters. Lowered to 80, comfortably under fetch's own gate, so the check only ever
    fires on content a caller managed to get past `fetch_source` some other way, not on ordinary
    pages that already passed Phase 03's own thin-content filter. Caught by
    `tests/integration/test_verify.py::test_web_200_is_verified` failing against the shared
    `PLAIN_HTML` fixture used across the fetch-layer's own tests.
  - **Test-isolation trap, real Postgres, no per-test rollback**: `verification_cache`,
    `entities`, and `entity_aliases` are ordinary tables with no `TRUNCATE`/rollback between test
    functions (matching this suite's established pattern — same reasoning as `unique_root()` in
    `tests/integration/_http.py`). A first draft reused literal scheme values (`"acme/widget"`,
    `"widget"`) across multiple tests in `test_verify.py`; once one test's *verified* result got
    cached, a later test expecting a fresh dispatch (e.g. asserting `RetrieverUnavailableError` with
    no retriever configured) silently short-circuited on the earlier test's cache hit instead of
    ever reaching the code path under test. Fixed by uniquifying every scheme value (`_slug()`,
    mirroring `unique_root()`) in both `test_verify.py` and `test_resolve_store.py` — worth
    restating for any future phase writing Postgres-backed tests against a cached/keyed table.
  - `make check`: 437 tests (up from Phase 06's 318), 97.27% coverage overall; `resolve/` module
    coverage: `alias.py`/`maturity.py`/`store.py`/`types.py`/`__init__.py` 100%, `verify.py` 96%,
    `entity_key.py` 99% (one defensive branch that the regexes it guards make genuinely
    unreachable). `pyproject.toml`'s coverage config extended with `src/api/resolve`, per the
    pattern every prior phase followed.
  - Full design/scope: [`docs/execution_phases/phase-07-entity-resolution.md`](execution_phases/phase-07-entity-resolution.md).

- **Implemented Phase 08**: `src/api/evidence/` — `grade.py` (`SourceKind`, a closed vocabulary
  over masterplan §4.6/§5's grading table for the two provenance decisions no retriever's own flat
  `grade` attribute already covers: own-domain fetches graded by path
  (`classify_own_domain_fetch` — structured A vs. prose/blog/changelog B, reusing Phase 03's
  `CHANGELOG_PATHS` as the single source of truth), and Wayback snapshots, which inherit the
  underlying artifact's grade capped at B), `confidence.py` (the masterplan §4.6 formula
  implemented verbatim — `BASE`/`DOMAIN_BONUS_PER_STEP`/`DECAY_PER_30_DAYS`/
  `CONTRADICTION_PENALTY`/`CONFIDENCE_CAP` all named, tunable constants — plus
  `distinct_domain_count` reusing Phase 07's PSL-aware `derive_web_key` so `docs.foo.com`/
  `www.foo.com` collapse to one domain, and `age_days` preferring a claim's own `as_of` over
  `fetched_at`), `contradictions.py` (the masterplan §4.7 `GROUP BY` with grade D excluded,
  resolution highest-grade-wins/tie-on-recency, losers retained via `superseded_by`, and the 0.6
  penalty recomputed onto the surfaced winner from its stored `confidence_inputs`), `promotion.py`
  (the two distinct anecdote thresholds — 5 comments/3 threads for community themes,
  reaction-weighted for GitHub with no breadth requirement), `coverage.py` (cost-weight-weighted coverage, failed vs.
  budget-skipped vs. other-skipped branches kept distinct, Phase 07's `insufficient_signal`
  entities folded in as a second, multiplicative signal). No orchestrating entry point like
  `resolve_entity` — the phase doc's own concrete output is "deterministic confidence on every
  claim, and a contradiction detector proven to fire", not a single pipeline function; each module
  is called independently by later phases (contradiction resolution once per completed run;
  promotion/coverage from Phase 11's synthesis stage). Migration `0008` adds one column,
  `claims.confidence_inputs jsonb` — the phase doc's own exit criterion that formula inputs be
  "stored per claim for auditability and recomputation". `make check`: 517 tests (up from 437),
  97.37% coverage overall, every `evidence/` module at 95–100%.
  - **Zero LLM calls, enforced structurally, not just by convention** — a new AST import-check
    test (`tests/unit/test_evidence_no_llm.py`, mirroring `api.sources.serp_snippets`'s own
    "no `httpx` import at all" test) asserts no `.py` file under `src/api/evidence/` imports
    `api.llm` or any of its submodules. Masterplan §4.6/§4.7's own framing — "a formula", "it is a
    `GROUP BY`" — is what makes this phase's constants visibly arbitrary rather than a model's
    opaque `0.82`, and this test is what keeps that true mechanically as the codebase grows.
  - **One deliberate, tracker-worthy design decision, surfaced before writing code**: the masterplan's
    literal contradiction-detection SQL only ever compares `value_num` with `DISTINCT`, but the
    closed vocabulary has plenty of non-numeric attributes (`pricing.model`, `company.stage`,
    `product.launch_date`, ...). `contradictions.py` extends the literal query with a per-attribute
    comparison rule — numeric on `value_num` (Postgres `numeric` is exact decimal, so `$5.00`/`$5`
    already compare equal with zero extra tolerance logic needed), everything else on normalised
    (trimmed, lowercased) `value_text` — computed in Python after one `GROUP BY` fetch rather than
    in the SQL itself, since the per-attribute branch isn't expressible in a single `DISTINCT`
    clause. `attribute_spec` (Phase 00) is the single source of truth for which branch fires.
  - **The contradiction penalty is applied to the winner, not the loser** — the phase doc's own
    Open Decision #1, left unresolved on purpose until Phase 13 settles how contradictions render;
    this phase implements the doc's stated current default (loser is superseded and not scored for
    display) and stores enough (`confidence_inputs` on every claim, not just winners) that
    switching later is a recomputation, not a re-extraction.
  - **`GITHUB_REACTION_THRESHOLD` (promotion.py) is a first-pass guess, not a masterplan number** —
    the masterplan gives only a worked example ("one issue with 47 thumbs-up clears the bar"), no
    threshold. Set to 20, named exactly like `api.resolve.maturity`'s own threshold constants,
    explicitly tunable in Phase 14 against real benchmark data rather than left as a hardcoded
    magic number.
  - **The trap-case integration test, the phase's signature test per the phase doc's own naming**:
    a live pricing page (grade A, `as_of` 2026-07-30, $5) vs. a 2025 aggregator review (grade C,
    `as_of` 2025-11-02, $18), seeded directly into Postgres — detected as one contradiction group,
    A wins on grade alone (recency tiebreak never even needed), C is retained with `superseded_by`
    pointing at A rather than deleted, and A's persisted `confidence` is verified byte-for-byte
    against `confidence()` recomputed with `contradicted=True` from A's own stored
    `confidence_inputs`. `tests/integration/test_contradictions.py`.
  - **A real edge case caught while writing the resolution tests**: a claim written without
    `confidence_inputs` populated (a caller that predates this phase, or simply forgot) must not
    crash contradiction resolution — `_apply_contradiction_penalty` logs a warning and returns
    without touching that claim's confidence, while loser retention and `superseded_by` still
    happen normally for the rest of the group. Covered explicitly
    (`test_missing_confidence_inputs_does_not_crash_resolution`) rather than left as an assumed-safe
    path; the one line this can't reach without a malformed jsonb value from outside Postgres
    (`_decode_confidence_inputs`'s `TypeError` branch) is `contradictions.py`'s only coverage gap
    (95%, vs. 100% on the other four modules).
  - `pyproject.toml`'s coverage config extended with `src/api/evidence`, per the pattern every
    prior phase followed. Migration `0007`'s `entities`/`entity_aliases`/`verification_cache`
    needed no changes — Phase 08 only ever reads `entity_id` off an already-resolved `Claim`.
  - Full design/scope: [`docs/execution_phases/phase-08-grading-confidence-contradictions.md`](execution_phases/phase-08-grading-confidence-contradictions.md).

- **Implemented Phase 09**: `src/api/planner/` — `interpret.py` (Stage 0: input validation — 300-char
  cap, injection-pattern/blocklist/non-product rejection, all before any model call — then a
  `structured()` call, the disambiguation decision), `plan.py` (Stage 1: brief -> seed `Plan`, one
  domain-level repair round, deterministic fallback), `registry.py` (the masterplan §4.1 `TASKS`
  registry plus which of the seven kinds a planner may actually seed), `validate.py` (the three DAG
  domain checks `api.models.plan.Plan`'s own pydantic invariants don't cover), `fallback.py` (the
  deterministic default plan). Two new prompts, `interpret_brief.md`/`plan_dag.md`. No migration —
  `runs.brief jsonb` (Phase 00) and the `tasks` table (Phase 02) already hold everything a plan
  needs to persist or replay; this phase is pure computation over the LLM gateway. `make check`:
  575 tests (up from 517; 58 of them `api.planner`'s own — 45 unit, 13 integration), ruff/mypy
  clean. Coverage on `src/api/planner/` could not be measured end-to-end in this session — see the
  Docker note below — but per-module: `registry.py`/`fallback.py`/`__init__.py` 100%,
  `validate.py` 92%, `interpret.py` 91%, `plan.py` 59% (`plan_stage1`'s async body is exercised
  only by the Postgres-gated integration tests, which skipped here); the unit suite alone already
  covers all pure logic (input validation, disambiguation ranking, domain validation, fallback,
  the `RawPlan`->`Plan` conversion boundary) with real assertions, not just import checks.
  - **`keywords` deliberately never became a field of `api.models.brief.ResearchBrief`,
    diverging from the phase doc's own Stage 0 design sketch.** The phase doc's `ResearchBrief`
    includes `keywords: list[str]`, but that model is reused verbatim as `Report.brief`
    (`api.models.report`), and `test_contracts.py::test_report_parses_masterplan_section_2_literal_unmodified`
    asserts `Report.model_dump()` matches the masterplan §2 JSON literal byte-for-byte — a literal
    with no `keywords` key, matching the masterplan's own output contract. Adding the field (even
    with a default) would have silently started emitting it on every report and broken that frozen
    Phase 00 regression fixture. Resolved by keeping `RawBrief` (the LLM-facing schema, in
    `api.planner.interpret`) as the superset and returning `keywords` as a sibling value on
    `InterpretResult` instead — the same "raw schema is a superset of the stored contract" split
    Phase 06 already established for `RawExtractedClaim` vs. `ExtractedClaim`. `keywords` is used
    only to seed Stage 1's `query_variants`/`mine_community` args, never persisted onto the brief.
  - **A real, non-obvious design gap in the phase doc, resolved and worth recording**: the doc
    says "the DAG is a seed... `discover_competitors` does not know entity keys yet, so it emits
    `profile_product` children dynamically at runtime", but doesn't say which other of the seven
    registry kinds share that problem. Working it through: `profile_product`, `extract_pricing`,
    `oss_profile`, and `find_funding` **all** require an `entity_key`/`repo` that cannot exist at
    planning time — no entity has been discovered yet — so **none** of the four can ever
    legitimately appear in a seed plan, not just `profile_product`. Only `discover_competitors`,
    `mine_community`, and `trend_signals` have every required arg knowable from the brief alone
    (`api.planner.registry.SEED_KINDS`). The planner's "how many competitors to profile" /
    "is GitHub relevant" / "does funding matter" judgements — real decisions the phase doc insists
    the planner still makes — are carried as three advisory fields on the `discover_competitors`
    node (`max_profile_count`, `consider_oss`, `consider_funding`) for Phase 10's handler to read,
    since there's no node to attach them to otherwise. Budget for the anticipated fan-out is
    reserved on that same node's `budget_weight` (base cost plus `max_profile_count` times the
    combined profile+pricing cost) rather than invented as phantom nodes, so `total_budget_weight`
    stays an honest sum the executor's real `BudgetTracker` (Phase 02) can still cap independently.
  - **Why Stage 1 targets a new `RawPlan`/`RawPlanNode` schema instead of `Plan` itself.**
    `PlanNode.args` is `dict[str, Any]` — fine in Python, but `Any` has no clean strict-mode JSON
    Schema representation, and `api.llm.gateway.structured()` always sends
    `response_format.json_schema.strict: true`. `RawPlanNode` gives every registry arg its own
    concretely-typed optional field (`query_variants: list[str] | None`, etc.) — verified by hand
    against `RawPlanNode.model_json_schema()` to produce the same `anyOf [..., {"type": "null"}]`
    shape already proven at 0/50 schema-violation rate for `RawExtractedClaim` in Phase 01's spike.
    `plan.py._to_plan` then builds a real `Plan`, which is where `Plan`'s own invariants (cycle,
    dangling edge, budget mismatch, missing declared args) get enforced as an ordinary
    `ValidationError` — folded into the *same* one repair round as the domain checks
    (`validate_plan_domain`), matching the phase doc's "schema parse -> DAG validation -> one
    repair -> fallback" pipeline as one linear flow rather than two independent repair layers.
  - **Plan-changing classification (`is_plan_changing`) is a static per-field table, not literal
    re-planning.** The phase doc's own rationale ("hypothetically re-plan with the alternative
    value and diff the DAG") is captured once as a constant (`PLAN_CHANGING_FIELDS` /
    `PLAN_DELTA_MAGNITUDE`), not executed per brief — actually calling Stage 1 twice per candidate
    field just to decide whether to ask a question would burn real LLM calls on every run and make
    a routing decision non-deterministic, which conflicts with the phase doc's own test spec
    exercising this as a pure function. `monetisation_guess` is absent from the table by
    construction, so it structurally cannot trigger a chip.
  - **A real bug found while writing the integration tests, not a hypothetical one**: the first
    draft of both `test_planner_stage0_gateway.py` and `test_planner_stage1_gateway.py` reused a
    literal query / literal keyword list across several tests. `api.llm.cache`'s response cache is
    deliberately permanent in the long-lived test Postgres container, so the second test to run
    against identical rendered prompt text silently got the *first* test's cached response instead
    of its own scripted one — reproduced locally with a throwaway fake-pool script before any real
    Postgres was involved (fallback resolved to `used_fallback=False` when it should have been
    `True`). Fixed with `uuid4`-suffixed uniqueness helpers (`unique_query`/`unique_keywords`),
    the same lesson `docs/working_knowledge.md` already records from Phase 04/05.
  - **Docker/Postgres were not available at the start of this execution session** (no `docker`
    binary, no passwordless `sudo` to install a local server) — same starting condition Phase 00
    hit before its own Docker Desktop WSL integration was enabled. All 13 new integration tests
    are written against real Postgres per convention (`ScriptedTransport` + `build_context`, no
    mocked Postgres); before Docker came up they skipped gracefully rather than failed, and were
    exercised once, out-of-suite, against a throwaway fake `asyncpg.Pool`-shaped object to catch
    real runtime bugs (it caught the cache-collision bug above) — that scratch script was
    discarded once real verification became possible. **Docker was enabled mid-phase and full
    verification followed**: `make db-up` (the `ai_pi_test` database and its migrations, up to
    `0008`, already existed from a prior session), then `make check` — **575 tests, 0 failures,
    97.35% coverage overall**, all 13 planner integration tests genuinely green against Postgres.
    `src/api/planner/` per-module coverage: `interpret.py`/`registry.py`/`fallback.py`/
    `__init__.py` 100%, `validate.py` 95%, `plan.py` 93%.
  - `pyproject.toml`'s coverage config (`addopts` and `[tool.coverage.run] source`) extended with
    `src/api/planner`, per the pattern every prior phase followed.
  - Full design/scope: [`docs/execution_phases/phase-09-interpreter-planner.md`](execution_phases/phase-09-interpreter-planner.md).

- **Implemented Phase 10**: `src/api/tasks/` — `context.py` (`HandlerDeps` bundling every shared
  client, `RunStats` run-level instrumentation counters), `claims.py` (`persist_extracted_claims`
  the LLM/span-bound path, `persist_structured_claim` + `get_or_create_synthetic_source` the
  structured-API path — both grade and confidence-score a claim before writing it, explicitly
  Phase 09's own Next Steps note 6: "wiring `grade_for`/`confidence` into claim construction... is
  Phase 10's job"), `discover.py` (`discover_competitors` — the fan-out root: search, optional
  GitHub `awesome-<category>` repo search, candidate filtering, `resolve_entity` verification,
  deterministic ranking, bounded `profile_product`/`extract_pricing`/`oss_profile`/`find_funding`
  spawn), `profile.py` (`profile_product` plus the shared `fetch_and_extract`/`resolve_entity_id`/
  `task_llm_cost` helpers `pricing.py` and `funding.py` reuse), `pricing.py` (`extract_pricing`),
  `community.py` (`mine_community`), `oss.py` (`oss_profile`), `funding.py` (`find_funding`),
  `trends.py` (`trend_signals`), `registry.py` (`build_registry` wiring all seven into Phase 02's
  `HandlerRegistry`). `src/api/cli.py` — `python -m api.cli run/inspect/replay`, owning the `runs`
  row lifecycle (no other module does yet — Phase 12 eventually puts an authenticated HTTP API in
  front of it), `Plan` -> `ExecutionPlan` adaptation, driving `Executor.submit`, and the phase doc's
  instrumentation printout. `api.sources.github.GitHubRetriever` gained two additive methods,
  `search_issues` (repo-agnostic, for `mine_community`'s `github` venue — `issues_by_reactions` is
  repo-scoped, built for `oss_profile`'s different use case) and `search_repositories` (the
  `awesome-<category>` seeding masterplan §5 names). No migration — every table Phase 10 writes to
  (`claims`, `entities`, `sources`, `tasks`) already exists; `claims.confidence_inputs` (Phase 08's
  migration `0008`) is exactly what this phase needed and already had. `make check`: 615 tests
  (up from 575), 96.14% overall coverage; `src/api/tasks/` per-module: `registry.py`
  100%, `pricing.py` 100%, `oss.py` 99%, `context.py` 99%, `discover.py` 96%, `claims.py` 93%,
  `profile.py` 88%, `funding.py` 84%, `trends.py` 84%, `community.py` 82% — every module clears the
  phase doc's own lower 80% bar for this package ("these are thin adapters, and the real assurance
  is the pipeline test").
  - **The milestone claim, proven twice**: once offline (`tests/integration/test_pipeline_e2e.py` —
    a real deterministic `fallback_plan`, zero LLM calls, driven through the *real* Phase 02
    `Executor` and the *real* `build_registry`, asserting every persisted claim's span is exactly
    its quote against its source's exact stored text, and that the hallucinated candidate never
    reaches `entities` while the verified one does) and once for real: `python -m api.cli run`
    against the live internet (OpenRouter, Exa, GitHub, and real vendor sites) — see the live-run
    entry below.
  - **A real, load-bearing budget-accounting bug, found by the pipeline e2e test, not guessed at.**
    Phase 09's `discover_competitors` node stores an *inflated* `budget_weight` on purpose — its own
    registry cost plus headroom for the `profile_product`/`extract_pricing` children it hasn't
    spawned yet (`api.planner.fallback`'s own comment: "budget for the anticipated fan-out is
    reserved on that same node's `budget_weight`... rather than invented as phantom nodes"). But
    `api.executor.budget.BudgetTracker` deducts a task's *own* `TaskSpec.budget_weight` from the run
    cap the instant that task is admitted — it has no concept of "this weight is a bucket my
    children will draw from later". Passing the inflated value straight through double-counted:
    `discover_competitors` alone consumed the entire run budget, and every spawned child was skipped
    for `"budget"` even on a plan sized exactly as the planner intended (first symptom: the pipeline
    test's `discover_competitors` completed but `finished.done` stayed at 1). Fixed at the Phase 10
    boundary, not in `api.planner` or `api.executor`: `api.cli.plan_to_execution_plan` charges a
    `discover_competitors` `TaskSpec` only its own registry cost
    (`TASK_COST_WEIGHT[DISCOVER_COMPETITORS]`); `Plan.total_budget_weight` (the inflated figure) is
    still what's passed as the *run's* overall cap to `Executor.submit`, so the headroom the planner
    reserved is exactly what's left for the real children to draw against. Every other seed kind's
    stored `budget_weight` already equals its own registry cost (nothing else spawns further
    children in v1), so the fix is a no-op for them.
  - **An operational gap, not a code bug, that the first live run attempt caught anyway**: this
    session's local `ai_pi` (the non-test dev database `Settings.database_url` actually points at)
    was still at migration `0007` — Phase 08's `0008` (`claims.confidence_inputs`) had only ever
    been applied to `ai_pi_test`. Every handler that persists a claim failed with `UndefinedColumnError`
    until `uv run alembic upgrade head` was run against the real dev DB — the exact trip Phase 06's
    tracker entry already named ("`alembic upgrade head` targets `ai_pi` by default, not
    `ai_pi_test`") happening again, one migration later, because nothing had exercised the dev DB
    against real code since Phase 08 shipped. `inspect` still rendered a fully correct, readable
    failure report for the crashed run — useful confirmation of its own value as a debugging surface
    even when the run itself failed.
  - **A second real bug, found only by the live run, not by any offline test**: raw transport
    failures (a DNS `NameResolutionError`/`httpx.ConnectError` for a search result naming a domain
    that no longer resolves) propagate uncaught out of `api.retrieval.fetch_source` — Phase 03 only
    wraps *timeouts* in a typed `FetchError`, not connection failures — all the way through
    `resolve_entity`, crashing `discover_competitors` entirely instead of dropping the one bad
    candidate. Reproduced with a scripted `httpx.ConnectError` step
    (`test_transport_failure_on_one_candidate_does_not_crash_discovery`) and fixed with a
    per-candidate `try/except (httpx.HTTPError, OSError)` around `resolve_entity` in `discover.py` —
    a deliberately Phase-10-local fix (masterplan Rule 4 / phase doc: "partial failure is normal";
    one candidate breaking is exactly that case, not a reason to touch Phase 03's frozen contract).
  - **A third live-run-only finding**: `mine_community`'s original 90s `timeout_s` was too tight for
    real sequential per-`(venue, keyword)` HTTP calls plus one LLM extraction call each (handlers
    don't parallelise their own calls — concurrency is the executor's per-service semaphores' job,
    not a handler's) — raised to 180s after observing a real timeout against live vendors.
  - **The same test-isolation trap every prior phase has hit, hit again while writing these tests**:
    `api.search.cache` has no `run_id` in its key by design (masterplan §9 — a repeat query should
    be nearly free, even across runs), so a literal query string or `ResearchBrief.category` reused
    across two tests (or two runs of the same test) silently replays a stale cached search response
    from an earlier invocation instead of hitting the freshly-scripted mock transport. Same fix as
    every phase before it: a `unique_query()` helper appending a `uuid4` suffix, used everywhere a
    query or category string feeds `SearchRouter.search`. Every new integration test file re-run
    twice in direct succession before trusting it, per the established discipline.
  - **`mine_community`'s claims need an `entity_id`, but the masterplan's own output contract
    doesn't attribute them to one** — a real schema/vocabulary tension, resolved rather than
    ignored. `claims.entity_id` is `NOT NULL` (Phase 00, frozen), but the Report contract's
    `pain_points`/`feature_gaps` (§2) carry no `entity_key` at all — community complaints are
    category-wide signal. Rather than force a real competitor onto every mined comment (wrong when
    it isn't actually about one) or extend `EntityScheme` unilaterally (a frozen contract this phase
    has no mandate to change), every `complaint.*`/`request.*` claim is attached to one synthetic
    per-run bookkeeping entity, `category:<run_id>`, built directly via `store.upsert_entity` —
    never routed through `resolve_entity`, and never intended to appear in a report's `competitors`
    list. Phase 11's theme clustering groups by `attribute`, not `entity_id`, so this is
    load-bearing only as bookkeeping. v1 does not attempt to detect whether a mined comment is
    actually about an already-discovered competitor (would let a complaint attach to a real entity
    when detectable) — left as an open item, partly because the phase doc's own Open Decision #2
    ("should `mine_community` run before profiling?") means there's no ordering guarantee a
    competitor even exists yet when this handler runs.
  - **`trend_signals` persists no claims at all — a real, undecided gap, surfaced rather than
    papered over.** The masterplan §4.4 closed claim vocabulary has no attribute for trend/volume
    data (nothing like `trend.<keyword>.volume`), and extending `ClaimAttribute` is a frozen Phase
    00 contract this phase has no mandate to touch. So `trend_signals` reports HN post volume and
    Wikipedia monthly pageviews via `HandlerResult.artifacts` only — which the executor does not
    persist anywhere durable (nothing in `core.py` reads `result.artifacts` beyond `result.spawned`)
    — meaning this handler's real signal does not currently survive past the run that collected it.
    Left as an explicit open item for Phase 11, which would need somewhere in the report to put it.
  - **`oss_profile`'s two honest gaps, both named rather than faked.** `oss.contributors_90d` is
    never populated: `GitHubRetriever.repo_metadata` only exposes a *total* contributor count (via
    the `contributors` endpoint's Link-header trick), not one scoped to the trailing 90 days, and no
    endpoint Phase 04 wired up provides that distinction — persisting the total under a `_90d`
    attribute name would misrepresent it. `oss.stars_90d_delta` is an approximation
    (`compute_star_velocity`'s stars-per-day rate × 90), because the Starring endpoint — the only
    source of exact per-star timestamps — still 403s under the current fine-grained PAT, open since
    Phase 01/04 and **now directly observed live** (see below), not just proven against a recorded
    cassette. This handler degrades that one field to "unknown" (no claim written) rather than
    failing the whole task, and separately refreshes the entity's `maturity` classification with the
    real signal it just fetched (`store.upsert_entity` always takes the freshest classification,
    Phase 07's Next Steps note 10(a)).
  - **Structured API values reuse the LLM extraction plumbing's own contract, not a shortcut around
    it.** `oss_profile` never calls the LLM — `oss.stars`/`oss.license`/`oss.last_commit_at` are
    exact numbers off GitHub's API — but `claims` still requires `quote`/`char_start`/`char_end`/
    `source_id` on every row (masterplan Rule 1 applies uniformly, not just to prose). `api.tasks.
    claims.persist_structured_claim` builds a short synthetic "page" summarising the API response,
    stores it as an ordinary `sources` row via `get_or_create_synthetic_source`, and binds a quote
    it wrote itself against that same text with the real, unmodified `bind_span` — deterministic,
    can never hit `quote_ambiguous`/`quote_not_in_source` by construction, since the caller controls
    both text and quote.
  - **Discovery's seed strategies are narrower than the phase doc's own list, logged as a scope
    note rather than silently implied otherwise.** `discover_competitors` seeds candidates from
    general search (`SearchRouter`) and, when `consider_oss`, GitHub `awesome-<category>` repo
    search (`GitHubRetriever.search_repositories`, new this phase). The phase doc also names the
    AlternativeTo competitor graph and package-registry search as seed strategies; neither is
    implemented — AlternativeTo has no retriever (Phase 04 built G2/Capterra as SERP-snippet-only,
    never AlternativeTo), and `PackagesRetriever` only checks a *named* package's download count, it
    has no search capability to discover unknown package names from a category. General search plus
    `awesome-` repos is the dominant channel per the masterplan's own framing ("search... reserved
    for discovery only"); recall impact, if any, is exactly what the phase doc's Open Decision #1
    defers to Phase 14's measurement rather than guessing at here.
  - **`Entity` candidates from a bad search hit are filtered *before* ever reaching verification**,
    not relied on to fail Rule 2 gracefully every time — `NON_CANDIDATE_DOMAINS` (aggregators,
    social platforms, `github.com` itself as a bare host) keeps obviously-non-competitor URLs (G2,
    Reddit, Twitter, ...) out of the ~24-candidate verification budget (`MAX_CANDIDATES_VERIFIED`)
    entirely, so a noisy result set can't crowd out real candidates by burning verification calls on
    domains that could never legitimately be a competitor anyway.
  - **Real runs against the live internet** (`python -m api.cli run`, real OpenRouter/Exa/GitHub/
    vendor traffic; local dev DB and Docker Postgres both happened to be available this session).
    Three attempts, kept as evidence rather than only the last clean one: run 1 (`"AI expense
    tracker for freelancers"`) surfaced the missing-migration issue below and confirmed `inspect`
    renders a correct DAG/claims view even for a run that never finished; run 2 (`"...v2"`) is what
    surfaced the raw-`ConnectError` crash fixed above; run 3 (`"...v3"`, post-fix) completed clean
    end to end: `run.finished: done=12 failed=0 skipped=5`, **21 entities verified, 3 rejected**
    (`ramp.com` `UnsupportedContentTypeError`, `expensebot.ai` `ThinContentError`,
    `papayaglobal.com` `http_403` — masterplan Rule 2 firing for real, not a fixture), **166 claims
    bound** (dropped: `quote_not_in_source=12, quote_ambiguous=4, invalid_attribute=1,
    value_type_mismatch=9`), **cost $0.0139/run** (`$0.0069` LLM + `$0.0070` search) — comfortably
    under the masterplan's ~$0.04 model — and **duration 279s (~4m39s)**, over the masterplan's
    3-minute promise at this session's default concurrency (`search=4, crawl=8, llm=6`) and
    `DEFAULT_MAX_COMPETITORS_PROFILED=8`; a real number for Phase 14 to tune against (the phase
    doc's own prescribed levers, in order: raise concurrency, cut `MAX_COMPETITORS_PROFILED`, cut
    `MAX_PAGES_PER_ENTITY`). Spot-checked a live claim byte-for-byte against Postgres directly
    (`substring(extracted_text from char_start+1 for char_end-char_start) = quote`) — true for every
    row checked, the core guarantee holding on real, not synthetic, page text
    (`pricing.entry_usd_month` bound to a real `"at a flat $19 / seat / month"` quote on a real
    vendor's real pricing page, among others). Fetches were **26 attempted, 26 cache hits** — not a
    bug: `resolve_entity`'s own `web:` artifact check already fetches the candidate's homepage to
    confirm it 200s, at the exact same canonical URL `profile_product`'s own homepage step needs, so
    within one run the homepage fetch is free by construction — a real, measured confirmation of the
    source-cache design, not assumed.
  - **A genuine, structural coverage-formula finding, visible only with real data**:
    `api.evidence.coverage.compute_coverage`'s `entity_score` term
    (`1 - insufficient_signal_entities / total_entities`) is **multiplicative** with task-weighted
    coverage — and every one of the run's 21 verified entities came back `insufficient_signal=true`
    (confirmed directly in `entities.meta`), driving the printed `coverage.score` to **0.00** even
    though 12/17 tasks (71%, matching the executor's own simpler count-based `RunFinished.coverage`)
    completed cleanly. Root cause, not a bug in the counting: `discover_competitors` resolves every
    candidate with an empty `MaturitySignals()` (nothing is known yet), and **no phase from 00
    through 10 has ever built a domain-age/install-count/download-count signal source for `web:`
    entities** — `oss_profile` is the only handler that ever refreshes maturity with real signals,
    and only for `gh:`-scheme entities. The practical consequence: `compute_coverage`'s entity term
    will structurally read `0.00` for essentially any real run today that discovers ordinary websites
    (the overwhelming majority of runs), which silently swallows the task-level partial-failure
    signal the phase doc's own Rule 4 ("a run whose funding branch died says so, out loud") depends
    on being informative. Not fixed here — building a domain-age source is new retriever surface
    (Phase 04-shaped work) this phase has no mandate for, and the formula itself is Phase 08's frozen
    contract — logged as a carried-forward, non-blocking-for-Phase-11 finding instead (Phase 11 only
    consumes `claims`/`entities` directly, not this score).
  - Full design/scope: [`docs/execution_phases/phase-10-task-handlers-e2e.md`](execution_phases/phase-10-task-handlers-e2e.md).
- **Implemented Phase 11**: `src/api/synth/` — `findings.py` (`build_all_findings`: four kinds per
  the phase doc's table — `pain_point` from clustered `complaint.<theme>` claims, `feature_gap`
  from clustered `request.<theme>` claims minus themes with a matching shipped
  `feature.<slug>.present` claim, `pricing_observation` across ≥2 entities, `competitor` from
  resolved non-synthetic entities; statements **templated, never generated** —
  `"{n} users across {t} threads report {theme}"`, real N only), `cluster.py` (the masterplan §11
  pgvector use: embed each theme slug + quote via `api.llm.embed`, union-find over pairwise cosine
  similarity ≥ `DEFAULT_SIMILARITY_THRESHOLD = 0.86`, most-frequent-slug label, support summed
  **after** clustering — the ordering the phase doc requires), `generate.py` (constrained
  synthesis, findings-only input — **never page text**: the prompt receives `[id] kind= support=
  confidence= statement` rows and nothing else; the first cheap gate is the model-self-reported
  `addresses_finding_ids` aggregate, rejected if it cites <3 distinct findings or zero
  `pain_point` findings — both masterplan §4.9 conditions; one repair attempt naming the specific
  violation, then the section is omitted; **no LLM call made at all when the finding set can't
  possibly satisfy the rule**, so a complaints-free run never pays for a doomed synthesis call),
  `bind.py` (the final gate: `pysbd` sentence segmentation — `$5.00/mo`/`e.g.`/`Inc.` covered by
  the proper segmenter, not a regex — parse each sentence's trailing `[3, 7]` citation marker,
  resolve `finding_id -> claim_ids` against the real finding set, **drop** unbindable sentences,
  omit emptied sections, strip markers from surviving prose), `assemble.py` (builds the §2
  `Report`: `build_competitors` requires the full pricing triple or excludes the entity — no
  fabricated field; `build_pricing_landscape` takes *every* `pricing.entry_usd_month` claim,
  independent of the ≥2-entity finding threshold; `build_contradictions` regroups Phase 08's
  winners/losers back into per-(entity, attribute) entries; `build_freshness` prefers `as_of` over
  `fetched_at`; `_assert_binding` **refuses to return or persist a report** whose prose lacks
  reachable `claim_ids` — the phase doc's "verified programmatically at assembly, not a test";
  `persist_report` upserts `reports.payload`). Plus `api.llm.embed.py` (the codebase's first
  embedding provider — OpenRouter `/embeddings`, `openai/text-embedding-3-small`, $0.02/M, the
  same vendor+credential as every chat call so no new vendor line item; cached permanently in new
  pgvector `embedding_cache` (migration `0009`, `vector(1536)`), costed through the existing
  `llm_calls` ledger as `prompt_id="embed_theme"` so `meta.cost_usd` picks it up for free),
  `LLMClient.embed()`/`RawEmbedding`/`_post_with_retries(url=...)` and `MODEL_RATES` entry in
  `api.llm.cost`, three new prompts (`synthesise_mvp.md`/`synthesise_gaps.md`/`synthesise_risks.md`,
  versioned with `schema:`/`cache_prefix_ends_after:` frontmatter, per-sentence citation-format
  instructions, repair via `{{repair_note}}` in the `## user` section), and `api.cli` wired to call
  `assemble_report` and persist the report at the end of every `run` (prints a one-line summary:
  competitors/pain points/gaps/risks/contradictions/mvp, and `coverage.failed_branches` on the
  report includes `assemble`'s own `synthesis.omitted_sections`). `make check`: **690 tests (up
  from 615), 0 failures, 96.51% coverage overall**; every `src/api/synth/` module 98–100%
  (`__init__` 100, `cluster` 100, `findings` 99, `assemble` 99, `bind` 98, `generate` 98) — the
  phase doc's 85% bar cleared with room to spare. The phase's signature test
  (`tests/integration/test_synth_pipeline.py`) walks every claim id the assembled report cites
  back to the real seeded source text and asserts the quote appears at the recorded span — the
  masterplan §1 interview sentence, in executable form.
  - **Two frozen Phase 00 `Report` leaf fields, widened and logged, not worked around** (the phase
    doc's own rule that contract breaks need a tracker note; full reasoning in the code):
    `CompetitorEntry.maturity` is now `Maturity | None` — `entities.maturity` is itself nullable
    and Phase 07's classifier returns `insufficient_signal` (no verdict) for essentially every
    real `web:` entity (Phase 10's finding), and `Maturity` has no "unknown" member, so an honest
    `None` replaces what would otherwise be a fabricated tier; `ContradictionValue.v` is now
    `float | str` — Phase 08 detects contradictions on non-numeric attributes (`pricing.model`,
    `company.stage`) via `value_text`, and a `float`-only field would silently drop half of what
    that module already finds. Both carried in the Current Status "logged, not hidden" line.
  - **The two required singular sections (`mvp`, `pricing_landscape`) get an honest degenerate
    value rather than a fabricated one.** The phase doc's "omit the section" works naturally for
    the list sections (`[]`), but `MVP` and `PricingLandscape` are singular and required by the
    frozen contract — so a run with no compliant synthesis gets
    `MVP(statement="", addresses_finding_ids=[])` and a zeroed `PricingLandscape`, and the
    omission is still visible on the report via `coverage.failed_branches`
    (`"mvp_synthesis"`/`"feature_gaps_synthesis"`/`"risks_synthesis"`), not silent.
  - **A real, silent bug the first drafts would have shipped: `{{repair_note}}` in the static
    prefix.** The `synthesise_*.md` prompts first placed the per-call-varying `{{repair_note}}` at
    the end of their `## instructions` section — inside `messages[0]`, which
    `api.llm.prompts.render_messages` builds from `template.static_prefix` alone, **never
    substituted against `variables`**. So the literal string `"{{repair_note}}"` sat unrendered in
    the system message on every call, identical whether or not a repair was in progress — and
    because `llm_response_cache` keys deterministically, a "repair" call whose *user* message
    (findings block) was unchanged from the first attempt hashed to the same cache key as the
    rejected response and **silently replayed it**: `generate`'s one-repair-round logic looked
    correct but never actually got a second, different answer. Caught by
    `tests/integration/test_synth_generate_boundary.py`'s repair-round test, not by inspection.
    Fixed by moving `{{repair_note}}` into each prompt's `## user` section (after
    `cache_prefix_ends_after`). Full write-up in `working_knowledge.md`'s Known Issues — this is
    now the documented rule: a per-call-varying placeholder must live in `## user` after
    `cache_prefix_ends_after`, or it is never substituted.
  - **A second real bug, in `api.llm.embed.embed_texts`: dedup at cache-lookup, not at
    request-build.** The first draft de-duplicated cache *lookups* but then built the vendor
    request from `texts[i]` per *position* still missing — two identical, both-cache-miss texts in
    one call were both sent to, and billed by, the vendor, contradicting the function's own
    "billed once" docstring. Caught by
    `tests/integration/test_llm_embed.py::test_embed_texts_sends_only_unique_texts_to_the_vendor`,
    which inspects the **literal request body** — a call-count assertion alone
    (`transport.calls[...] == 1`) would have passed, since the bug was about *what* was sent in
    that one call, not how many calls were made. Fixed by building one `(key, representative
    text)` pair per still-missing key before ever calling the vendor.
  - **A real, structural gap in the promotion-signal chain, named in code rather than silently
    approximated as if it weren't one.** Masterplan §4.6's promotion rules assume per-claim thread
    identity (and per-issue reaction counts for GitHub) survive to the claim. They don't:
    `api.tasks.community` bundles up to `max_community_threads` real threads/issues from one
    `(venue, keyword)` pair into a *single* synthetic source, so a `complaint.*`/`request.*` claim
    has no surviving link to which real thread it came from. `api.synth.findings` therefore treats
    **distinct `source_id` among a cluster's claims** as the thread-breadth proxy for
    `api.evidence.promotion.evaluate_community_theme`, uniformly for every venue — and
    `evaluate_github_theme` (reaction-weighted, no breadth requirement) has **no real caller in
    v1**, because no per-issue reaction count survives to a claim for it to consume. Both are
    *undercounts* of true breadth (many real threads collapse into one synthetic source), never
    overcounts, so promotion stays conservative in the direction the phase doc's own risk table
    prefers. Fixing properly needs `api.tasks.community` to persist real per-claim thread/issue
    identity — out of this phase's scope, carried in Next Steps.
  - **The generic-advice guard fired for real on the live run, exactly as designed.** `python -m
    api.cli run "WhatsApp first CRM for Indian SMBs"` (real OpenRouter chat + the real new
    `/embeddings` endpoint, real Exa/GitHub/vendor traffic): **49 claims bound** (span-checked as
    before), **8 findings** — 7 `competitor`, 1 `pricing_observation` (`$1254.50/mo` median across
    2 entities), **zero `pain_point` findings** (no complaints survived promotion), so `generate`
    correctly made **no synthesis calls at all** (`_can_possibly_satisfy` short-circuit) and the
    report's `mvp`/`feature_gaps`/`risks` came out empty, recorded in `coverage.failed_branches`
    as the three `*_synthesis` branches — a report with no MVP is honest, the phase doc's exact
    words. The rest of the report was real and correct: 2 competitors with complete pricing
    triples (`hellogrowthcrm.com` seat@$10, `connectribe.com` flat@$2499), 3 contradictions
    surfaced (including a `pricing.entry_usd_month` 12-vs-10 and a `pricing.model` `usage`-vs-
    `seat` — the `v: str` widening earning its keep on a real, non-numeric contradiction),
    `freshness.median_source_age_days: 0` (all fresh), `meta.cost_usd: $0.0399` (LLM $0.0049 of
    it — 27 calls incl. 1 `embed_theme` call billed $0.000001; the rest Exa/search), `duration_s:
    201.7`. Two earlier phase-11 attempts had already been silently protected by the test suite:
    the `{{repair_note}}` bug and the embed-dedup bug above were both caught by tests *before*
    this live run, not by it — the same pattern as Phase 10's live run catching things the
    previous live run taught us to check.
  - Full design/scope: [`docs/execution_phases/phase-11-synthesis-report-assembly.md`](execution_phases/phase-11-synthesis-report-assembly.md).
- **Phase 11 credential wrap-up (2026-08-07, post-commit)** — two carry-over credentials were
  obtained and verified live, and the third turned out to be unfixable as a credential change:
  - **Product Hunt token obtained and verified live.** `post_by_slug` returns real data against
    the real endpoint, and the cassette
    `tests/fixtures/cassettes/producthunt_post_by_slug.yaml` was recorded (authorization
    REDACTED), upgrading the retriever from MockTransport-only to cassette-tested — the first time
    this vendor has real traffic coverage since Phase 01 flagged it PENDING. **While verifying, two
    real problems surfaced and were fixed:** (1) `search_posts` was **broken on arrival** — the
    query declared `$query` but never used it, so GraphQL rejected every call with
    `variableNotUsed` and the method silently always returned `[]`; and (2) Product Hunt v2
    GraphQL has **no text-search field at all** (query root verified by schema introspection:
    `collection`/`collections`/`comment`/`post`/`posts`/`topic`/`topics`/`user`/`viewer`), so a
    search method was unimplementable-as-designed, not merely buggy. Removed `search_posts` (no
    caller exists; only `post_by_slug`, used by Phase 07's `ph:` verification, is real), updated
    the module docstring, and deleted its dead test. The carry-over item "Product Hunt developer
    token (still not started)" is **closed**.
  - **GitHub Starring endpoint: verified the "upgrade the PAT" fix cannot work — it is a
    permanent vendor restriction, now closed as such.** The intended fix was to add an explicit
    Starring permission to the fine-grained PAT. Live verification 2026-08-07: the token's
    `/user` and `/repos/{owner}/{repo}` both return 200, but `/stargazers` still returns 403
    (`"Resource not accessible by personal access token"`, rate limit remaining fine). Root cause
    is a GitHub platform change, not a credential gap: since **2026-06-30 GitHub restricts
    `/stargazers` to repo admins and collaborators only**, fine-grained PATs are **not supported
    for that endpoint at all** (no fine-grained permission exists for it; a classic PAT with
    `public_repo` works only when the token's owner is an admin/collaborator of the target repo).
    We are never that for competitor repos, so `star_velocity_90d` is **permanently degraded** —
    no PAT change can unblock it; only total `stargazers_count` (via `repo_metadata`) remains
    readable. Code and docs updated to say the real cause: `src/api/sources/github.py`'s module
    docstring + 403 message, `docs/external_apis.md` (Go/No-Go table + credentials table), and
    `docs/working_knowledge.md`'s Known Issues entries. Next Steps item 2 and 16(d) rewritten from
    "config fix needed" to "permanent vendor restriction, no fix". The carry-over item "GitHub PAT
    Starring-permission upgrade (open since Phase 01)" is **closed as unfixable-by-credential** —
    not silently dropped, recorded as a vendor change with the measurement that proves it.
  - **Reddit dropped as a source** — its manual 2–4 wk app-approval process made it infeasible
    (see Key Decisions D5); no integration or credential remains in the codebase. `make check`
    green after the Product Hunt changes.

### 2026-08-08

- **Implemented Phase 12**: `src/api/web/` — `auth.py` (`JWKSCache` — caches Supabase's JWKS keyed by
  `kid`, refetches the whole set only on a `kid` miss, never per-request; `verify_token` maps each of
  PyJWT's rejection exceptions to its own stable `code` — `token_expired`/`wrong_issuer`/
  `wrong_audience`/`bad_signature`/`malformed_token`/`unknown_kid` — each independently tested;
  `provision_user` is one round trip either way via `INSERT ... ON CONFLICT DO NOTHING` unioned with a
  fallback read, so first-login-creates / later-logins-reuse needs no separate exists-check),
  `quota.py` (`try_create_run` — see the atomicity bug below; `ConcurrencyQueue` — a FIFO in-memory
  admission gate with a queryable 1-based `position()`), `killswitch.py` (`system_state` singleton
  read/trip/reset — no admin HTTP endpoint, since the phase doc's own Endpoints table names none;
  an operator action against the table directly, "a database flag, not a deploy"), `turnstile.py`
  (Cloudflare siteverify; unset `turnstile_secret_key` is a no-op pass, the same "`None` means
  unconfigured" convention as every other optional credential in `api.config.Settings`), `sse.py`
  (`persist_event`/`read_new_public_events`/`stream_events` — the masterplan §4.10 six-event public
  vocabulary, a deliberately different, smaller set than `api.executor.protocol.ExecutorEvent`, both
  sharing the same Phase 02 `run_events` table; `stream_events` closes after `report.ready` or once
  `runs.status` turns `failed`), `runner.py` (`run_pipeline` — the same interpret → plan → execute →
  synth pipeline `api.cli.cmd_run` drives, reusing its `build_deps`/`plan_to_execution_plan`/
  `run_coverage` rather than re-deriving them, adapted to run as a background task and to genuinely
  pause on disambiguation — `status='needs_input'` until `PATCH /runs/{id}` supplies the resolved
  brief, the "ordinary HTTP round trip" the phase doc's Design section describes, not an in-graph
  interrupt), `errors.py` (typed `APIError` hierarchy, one JSON envelope shape, a correlation id on
  every response, never a stack trace or vendor message), `app.py` (`create_app(settings, pool, http)`
  — pool/http always caller-built, no FastAPI `lifespan=`, so exactly one place ever owns closing
  them), `main.py` (production `uvicorn.serve()` entrypoint), `routes/{runs,reports,health}.py`.
  Migration `0010` adds `system_state` (the kill-switch singleton), `runs.keywords`/
  `runs.disambiguation_fields`, widens `runs.status`'s CHECK to add `needs_input`, and indexes
  `runs (user_id, started_at)` / `runs (started_at)` for the quota-window counts. `make check`:
  **729 tests** (up from 690), **96.28% coverage overall**; every `src/api/web/` module 91–100%
  except the intentionally-excluded `main.py` (see Current Status).
  - **A real concurrency bug found and fixed by the atomicity test itself, not by inspection** —
    matching this project's own repeated lesson (Phase 02's dead-branch race, Phase 04/05/06's
    shared-cache contamination): the masterplan §8.3 SQL sketch for the quota check
    (`INSERT ... SELECT ... WHERE (SELECT count(*) ...) < quota`) is a single SQL statement and
    *looks* atomic, but under Postgres's default `READ COMMITTED` isolation it is not — `N`
    simultaneous connections each evaluate the subquery against the same pre-insert snapshot, so a
    quota of `N-1` first-draft-admitted all `N` (`8 == 8`, not `8 == 7`), reproduced deterministically
    by `asyncio.gather`-ing `N` real concurrent calls against real Postgres, not simulated. Fixed with
    two `pg_advisory_xact_lock`s (transaction-scoped, auto-released on commit/rollback) serializing
    the check: a fixed key for the global cap, then `hashtext(user_id)` for the per-user cap, always
    acquired in that order — so no two callers can ever deadlock against each other — and each
    skipped entirely when its corresponding cap is `None` (unenforced), so an unconfigured quota
    costs nothing. **Actionable finding for future phases: a conditional `INSERT...SELECT` that reads
    right is not proof of atomicity under concurrency — same spirit as Phase 02's "a single green run
    is weak evidence," but for isolation levels instead of timing.**
  - **A second real, repeatable bug, in `httpx.ASGITransport`-based test setup, not application
    code**: the first draft of the "errors never leak internals" test raised inside a route handler
    and expected the response to come back as a normal 500 — instead httpx re-raised the exception
    into the test itself. Root cause: Starlette's `ServerErrorMiddleware` sends the registered 500
    response *and* re-raises (intentional, so the ASGI server logs it), and `httpx.ASGITransport`'s
    default `raise_app_exceptions=True` propagates that re-raise into the caller rather than treating
    the already-sent response as the result. Fixed by passing `raise_app_exceptions=False` in the
    shared test client helper — worth restating for any future phase testing a FastAPI 500 handler
    through `ASGITransport`.
  - **A third real, repeatable bug, in the heartbeat test's own transport choice**: `httpx.
    ASGITransport` buffers a response's *entire* body before returning it to the caller, so a test
    reading `client.stream(...)` against a genuinely never-ending SSE generator hung forever rather
    than observing the ping frames as they arrived. Fixed by driving the ASGI callable
    (`EventSourceResponse.__call__(scope, receive, send)`) directly with a collecting `send`, no
    `httpx`/`ASGITransport` involved — the only way to observe an in-progress infinite stream inside
    this test framework.
  - **A fourth real, repeatable bug — the fifth phase in a row to hit the "shared, persistent table"
    trap this project's own `docs/working_knowledge.md` already documents** (Phase 04, 05, 06, and
    now 12): `needs_input` is a status value this migration adds that a *pre-Phase-12* downgrade's
    narrower `CHECK` constraint cannot accept. The disambiguation-pause tests correctly created rows
    in that status — and then, being real rows in the shared, long-lived `ai_pi_test` database, they
    silently broke `test_migrations.py`'s full downgrade-to-`0001`-and-back-up cycle for **every test
    that ran afterward**, in a completely different file, on a completely unrelated run of the suite.
    Not caught by a single green run (the polluting test and the victim test never appear in the same
    failure) — caught by this project's own "re-run twice" convention and by directly querying
    `system_state`/`runs` for rows outside the historical vocabulary. Fixed by having every test that
    creates a `needs_input` row clean it up (`DELETE`/transition it) in a `finally` block.
    **Actionable finding: any new enum/status value a migration adds is itself a "shared persistent
    state" hazard for `test_migrations.py`'s downgrade cycle, not just for cache/ledger tests — audit
    for it the same way.**
  - **One scope decision, made explicitly rather than silently dropped**: the phase doc's Endpoints
    table names no route for flipping the kill switch manually ("it can also be flipped manually" —
    no HTTP surface specified), so none was built; `api.web.killswitch.trip`/`reset` are directly
    callable functions an operator (or a future Phase 15 ops script) can drive against `system_state`.
    Similarly, `PATCH /runs/{id}` (the disambiguation-resolution endpoint) is real and tested but gets
    lighter-weight coverage than the rest of the surface — three tests (resolve-and-resume, conflict
    on a non-paused run, cross-user rejection) rather than the phase doc's own exhaustive per-field
    disambiguation matrix, a deliberate scope call given the walking-skeleton pattern this project
    already uses elsewhere for secondary paths (e.g. Phase 04's coverage-floor modules).
  - **`run_pipeline`'s own tests mock only the three points that would otherwise need real
    OpenRouter/Exa traffic** (`interpret`, `plan_stage1`, `assemble_report`) — `build_deps`, the real
    `Executor` (driven with an intentionally empty `Plan`, so it drains in milliseconds with zero task
    handlers), `resolve_contradictions`, and `run_coverage` all run for real, since every one of them
    is pure construction or a plain Postgres query with no external I/O. Same fixture-corpus spirit as
    Phase 01/05: keep real traffic out of the default tier without faking more than necessary.
  - Full design/scope: [`docs/execution_phases/phase-12-api-auth-quotas.md`](execution_phases/phase-12-api-auth-quotas.md).

- **Implemented Phase 13**: `web/` — a new Next.js App Router + TypeScript project, entirely separate
  npm toolchain from `src/api/`. `app/page.tsx` (homepage, `export const dynamic = "force-static"` —
  the ten benchmark reports are baked into HTML at `next build` time, zero runtime backend
  dependency), `app/r/[runId]/page.tsx` (report view, client-rendered — a permalink may point at a
  private run, so auth is a browser-session concern, not a build-time one), `app/new/page.tsx` (query
  input → Supabase sign-in gate → disambiguation chips → live SSE checklist → report). `components/`:
  `SpanHighlight` (the critical component — renders `[char_start,char_end)` correctly across a
  code-point/UTF-16 divergence), `SourcePanel` (the demo — source URL, fetched date, grade badge,
  highlighted quote, the confidence formula spelled out, "other claims from this source" navigation,
  focus-trapped + `Escape`-closable), `PlanChecklist`, `ReportView`, `CitedSentence`/`CitedFinding`,
  `ContradictionCard`, `CoverageBanner`, `DisambiguationChips`. `lib/`: `api.ts` (typed client
  mirroring the backend's Pydantic models field-for-field), `sse.ts` (fetch-based SSE client — not
  `EventSource`, since a non-public run's stream needs an `Authorization` header `EventSource` can't
  send — with explicit `Last-Event-ID` reconnect), `span.ts` (`cpToUtf16`, `resolveHighlightSpan`),
  `checklist.ts` (streamed `task.*` events → per-node checklist state), `confidence.ts` (mirrors
  `api.evidence.confidence`'s constants for display only), `supabase.ts` (lazy browser client, so
  static homepage generation never touches Supabase). 33 vitest unit tests, 30 Playwright E2E tests
  (15 specs × `chromium` + `mobile-chrome`), `tsc --noEmit` and `eslint` both clean.
  - **Three backend contract gaps found and fixed before the frontend could be built at all** — the
    same "surface a real gap, extend the earlier phase minimally, log it" pattern Phase 07 used on
    `GitHubRepo`/`ProductHuntRetriever`. Phase 12's `GET /runs/{id}/claims/{claim_id}` was missing
    every field the phase doc's own drill-down design requires showing: **(a)** `grade`/
    `confidence`/`confidence_inputs` — without them the "turns a number into an argument" panel
    (masterplan §12.5) has no argument to show; **(b)** `source_fetched_at` — the design calls for
    "source URL, fetched date, grade badge" and the endpoint had no fetched date; **(c)** "other
    claims from this source" — named directly in the phase doc's drill-down interaction diagram, so a
    small sibling-claims query was added. A fourth gap needed a genuinely new endpoint, not just wider
    fields: `MVP`/`Risk`/`FeatureGap.addresses_finding_ids` name a `findings.id`, and no route could
    ever turn that into a `claims.id` — `GET /runs/{id}/findings/{id}` was added
    (`findings_must_cite`'s `CHECK (cardinality(claim_ids) >= 1)`, migration `0001`, guarantees it
    always resolves). All four changes are additive to Phase 12's response models, covered by new
    integration tests in `tests/integration/test_api.py`
    (`test_drilldown_shows_confidence_inputs_and_other_claims_from_same_source`,
    `test_finding_drilldown_resolves_to_its_claim_ids`), `ruff`/`mypy --strict` clean. **Not verified
    against live Postgres in this session** — this sandbox has no Docker (`docker: command not
    found`), unlike every prior phase's own verification pass; the new tests collect correctly (731
    collected, up from before) and follow the exact query/model shape of the surrounding endpoints,
    but need a real `make integration` run before being trusted the way Phase 00's DB-backed fixes
    were.
  - **The public SSE contract has no field a checklist can use to deterministically match a streamed
    `task.*` event back to a specific `PlanNode`.** `api.executor.protocol`'s *internal* telemetry
    carries `node_key`, but `api.models.events.TaskStartedEvent`/etc. (the public vocabulary
    `api.web.sse` actually persists and streams) only ever carry `task_id: int` + `kind: TaskKind` —
    discovered while designing `PlanChecklist`, not by inspection first. `lib/checklist.ts`'s
    `reduceChecklist` matches best-effort by `kind`: the first still-pending node of a streamed
    event's `kind` is assigned that `task_id` the moment it starts, and every later event for that
    `task_id` updates the same row — correct because same-kind nodes are fungible `SKIP LOCKED`-leased
    work, not because the mapping is exact. A future phase wanting an exact mapping would need to add
    `node_key` to the public event vocabulary, which Phase 13 deliberately did not do (out of scope,
    and the approximation is invisible to a user — a checklist item ticking is either "some
    `profile_product` finished" or "the specific one," and only the backend can tell the difference).
  - **Disambiguation chips can't offer the mockup's literal alternative options.** Masterplan §3's
    `[ B2B ✓ | B2C ]` mockup implies a closed alternative set per field, but `select_disambiguation_
    fields` (`api.planner.interpret`) only ever reports *which* fields are low-confidence — `segment`/
    `geography` are free text on `ResearchBrief`, not enums, and the interpreter never emits
    alternatives. `DisambiguationChips` renders the model's best guess as a pre-selected, editable
    pill instead of an invented multiple-choice set — "ignorable, sensible default" is preserved
    (pressing Go untouched sends no overrides at all), but "pick from two options" isn't, because the
    API genuinely has nothing to pick from. Logged here rather than silently deviating from the
    masterplan's own mockup.
  - **E2E testing had no live Postgres, Supabase project, or FastAPI process available in this
    session** (same Docker gap as above) — resolved with two different mocking strategies, chosen for
    what each test actually needs, not one blanket approach: `tests/e2e/mock-server.ts`, a real (not
    `page.route`-stubbed) Node `http` server, answers the homepage's `next build`-time server-side
    fetch — `page.route` is a browser network hook and structurally cannot see a fetch that happens
    inside the Node build process, so a real listening server was the only way to prove the static
    homepage actually bakes in real data rather than only its empty-state fallback. Every other
    endpoint (run creation, polling, SSE) is mocked per-test via `page.route` in `tests/e2e/
    fixtures.ts` instead, so each test can parametrise run status / SSE pacing independently. One
    genuine test-design bug caught by a first failing run: the live-run "checklist ticks" test
    delivered its whole scripted SSE body as one instant `page.route` chunk, collapsing `plan.created`
    → `report.ready` into a single browser tick — too fast for Playwright's own poll loop to ever
    observe the intermediate `running` state, a timing artifact of the mock, not of the real pipeline
    (whose events are genuinely seconds apart). Fixed by giving `mock-server.ts`'s `/events` route
    real ~250ms delays between writes for that one test. The authenticated flow
    (`tests/e2e/live-run.spec.ts`) seeds a Supabase session directly into the `sb-<project-ref>-auth-
    token` `localStorage` key `@supabase/supabase-js` v2 reads on init, since no real OAuth redirect
    is possible here — flagged in `web/README.md` as needing re-verification against a real Supabase
    project before trust. WebKit could not be installed (`playwright install webkit` needs system
    libraries this sandbox has no root for) — `mobile-chrome` (`devices["Pixel 7"]`) stands in for the
    mobile-viewport checks instead of `devices["iPhone 14"]`.
  - Full design/scope: [`docs/execution_phases/phase-13-frontend.md`](execution_phases/phase-13-frontend.md).

## Ongoing Work

- [x] Phase 00 — Foundation, Contracts & CI (complete; `make check` green including all
      Postgres-backed integration tests against live Postgres)
- [x] Phase 01 — Dependency Validation Spike (complete; two non-blocking credential items open —
      see Current Status)
- [x] Phase 02 — Executor Core (complete; chaos suite green, flake-free at 30x local repeat,
      50x nightly in CI)
- [x] Phase 03 — Fetch, Text Extraction & Source Cache (complete; path-guess hit rate measured
      at 75%, real number carried into Phase 14's quota math — see Recent Activities)
- [x] Phase 04 — Search & Domain Retrievers (complete; Exa behind `SearchProvider` with credit-
      ledger allowance tracking, seven domain retrievers, GitHub star-velocity 403 proven to
      degrade cleanly against the real cassette — see Recent Activities)
- [x] Phase 05 — LLM Gateway (complete; `structured()` is the sole, lint-enforced entry point for
      every model call; live checks confirm 0/20 schema violations and prompt-cache hits landing
      2/10 — see Recent Activities)
- [x] Phase 06 — Claim Extraction & Span Binding (complete; 100% span-verified, drop-rate metric
      wired, extraction cache re-binds on every read — see Recent Activities)
- [x] Phase 07 — Entity Resolution & Identity (complete; `.fly.dev` signature scenario passes,
      alias-merge order-independence proven by Hypothesis property test — see Recent Activities)
- [x] Phase 08 — Grading, Confidence & Contradictions (complete; the masterplan's named trap case —
      live grade-A pricing vs. stale grade-C aggregator — passes end to end; zero LLM calls
      enforced by an AST check — see Recent Activities)
- [x] Phase 09 — Interpreter & Planner (complete; DB-backed — 575 tests green against real Postgres,
      97.35% coverage overall — see Recent Activities)
- [x] Phase 10 — Task Handlers & End-to-End Run (⭐ walking skeleton, complete; proven both offline
      via the real Executor and for real against the live internet — see Recent Activities)
- [x] Phase 11 — Findings, Constrained Synthesis & Report Assembly (⭐ product complete, complete;
      `Report` matches the masterplan §2 contract, 100% sentence-to-claim binding asserted at
      assembly and verified by the pipeline test walking every cited claim back to a real span in
      real source text; live-verified against real OpenRouter chat + the new `/embeddings` endpoint
      — see Recent Activities)
- [x] Phase 12 — API, Auth, Quotas & Guardrails (complete; authenticated FastAPI HTTP layer with SSE,
      atomic quotas proven under real concurrency, a database-flag kill switch, and Cloudflare
      Turnstile in front of the existing pipeline — see Recent Activities)
- [x] Phase 13 — Frontend & Drill-Down UI (complete; Next.js App Router app in `web/`, static
      benchmark homepage, live SSE checklist, and the drill-down panel with code-point-correct span
      highlighting — 33 unit + 30 E2E tests green; two Phase 12 backend gaps closed along the way —
      see Recent Activities)

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
| D5 | Reddit as a routine Tier-2 source | Self-service registration closed; 2–4 week manual approval | Reddit dropped entirely — the manual approval process was infeasible, so no Reddit integration ships; HN + GitHub + Stack Exchange are the backbone |

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
Migration trigger: measured 2026-08-10 per-run storage is **~0.40 MB** (not above 1.2 MB — the
escape hatch is NOT triggered on storage grounds; at 0.40 MB/run the 500 MB ceiling holds ~1,250
runs). Re-evaluate only if a new large-payload column lands, or the keepalive proves unreliable.

### Search: single provider, deliberately

Exa is the only search provider in v1. This gives up the index independence the earlier
Serper + Tavily pairing had. Accepted because the domain retrievers (HN Algolia, GitHub,
Stack Exchange, Wayback, package registries) are independent of Exa entirely, so an outage
degrades discovery rather than stopping a run.

**Search cost changed shape:** metered per-query → fixed monthly allowance. It cannot produce a
surprise bill, but it *can* run out mid-month. Consequence: budgets track **credits, not query
counts**, and `GLOBAL_RUNS_PER_DAY` now protects the allowance rather than the wallet.

### Cost model

**under $5/month fixed**, **~$0.012/run measured for LLM** (Phase 01, 60-page extrapolation —
better than the masterplan's $0.03 estimate) + **~$0.056/run for search** (Exa, ~8 queries/run at
$0.007 flat), both well inside the $10/mo Exa allowance (~179 runs/month ceiling) and the
essentially-free LLM budget. Path-guessing hit rate came in at 82% (Phase 01, real 40-page corpus)
— above the masterplan's assumption, so the ~4× search-volume-inflation risk did not materialize.

## Next Steps

 1. **Reddit is closed as a source (D5).** Its manual 2–4 week app-approval process made it
    infeasible, and the codebase carries no Reddit integration. Product Hunt is **closed** in the
    other direction (developer token obtained and verified live 2026-08-07) — `ProductHuntRetriever`
    has a tested `RetrieverUnavailableError` degradation path when no token is configured, so
    obtaining that credential is a config change, not a code change.
 2. **GitHub Starring endpoint: resolved 2026-08-07 as a permanent vendor restriction, not a
    credential gap.** The intended "upgrade the PAT" fix cannot work: since 2026-06-30 GitHub
    restricts `/stargazers` to repo **admins and collaborators only**, fine-grained PATs are **not
    supported for it at all** (no fine-grained permission exists), and a classic PAT with
    `public_repo` works only when the token's owner *is* an admin/collaborator of the target repo —
    never true for competitor repos. A fine-grained Starring permission has no effect (verified
    live 2026-08-07: `/user` and `/repos/{o}/{r}` return 200, `/stargazers` still 403).
    `star_velocity_90d` therefore stays permanently degraded to a coverage gap; only total
    `stargazers_count` (via `repo_metadata`) remains readable. Phase 10's `oss.stars_90d_delta`
    stays an approximation. Code/docstrings updated to say the real cause — see
    `docs/external_apis.md` and `src/api/sources/github.py`.
 3. ~~Begin Phase 07 — entity resolution.~~ **Done** — see Recent Activities. Turned out to be the
   actual consumer of Phase 06's `ExtractedClaim.candidate_entity_hint` in name only: Phase 07
   itself resolves an `EntityEvidence` into a persisted `Entity`, but wiring `candidate_entity_hint`
   into a real `resolve_entity` call, and turning the result + `ExtractedClaim` into a graded,
   confident `api.models.claims.Claim` row, is Phase 08 and Phase 10's job — Phase 07's own scope
   note explicitly excludes "claim attribution to entities beyond the resolution step".
4. ~~Begin Phase 08 — grading, confidence & contradictions.~~ **Done** — see Recent Activities.
   `api.evidence` is pure arithmetic/SQL over already-persisted claims, deliberately with no
   orchestrating entry point of its own: it does not itself consume `ExtractedClaim`/`Entity` to
   construct `Claim` rows. Wiring `grade_for`/`confidence` into claim construction, and calling
   `resolve_contradictions` once per completed run, is Phase 10's job — Phase 08's deliverables
   are the five independent modules that make grading/confidence/contradictions mechanical, not a
   pipeline that calls them.
5. Phase 00's contracts (`src/api/models/`) remain frozen; Phase 02, 03, 04, and 05 all added their
   own new types instead of touching them (`api.executor.protocol`, `api.models.source.Source`,
   `api.search.base.SearchResult`/`SearchResponse`, `api.llm.gateway.LLMResult`/`LLMContext`, plus
   every retriever's own record models) — see Recent Activities. All four extended the *schema* via
   migration (`0002_executor_core`, `0003_fetch_source_cache`, `0004_search_domain_retrievers`,
   `0005_llm_gateway`), logged there per the phase doc's rule that schema changes need a tracker note.
   Phase 07 followed the same pattern with its own `EntityEvidence`/`VerificationContext`/`Entity`
   types (`api.resolve.types`, `api.resolve.store`) and migration `0007`. Phase 08 followed the
   same "extend, don't touch the frozen contract" pattern with its own `ConfidenceInputs`/
   `ContradictionResolution`/`TaskOutcome`/`CoverageResult`/`PromotionResult` types
   (`api.evidence.*`), and, like Phase 03's `sources.etag`/`last_modified` before it, migration
   `0008` *alters* an existing Phase 00 table (`claims.confidence_inputs jsonb`) rather than only
   adding new ones.
6. ~~When Phase 10 builds real task handlers, it must adapt `api.models.plan.Plan` into the
   executor's `ExecutionPlan`/`TaskSpec` at the boundary...~~ **Done** — see Recent Activities.
   `api.cli.plan_to_execution_plan` does the adaptation, and found a real budget-double-counting
   bug doing it (`discover_competitors`'s inflated `budget_weight` vs. the executor's per-task
   deduction) — fixed there, not in `api.planner` or `api.executor`. `RetrievalBudget.spend_fetch()`
   is wired into every real `fetch_source`/`guess_path` call in `api.tasks.profile.fetch_and_extract`
   and `api.tasks.funding`. Every LLM call in `api.tasks` goes through `api.llm.gateway.
   build_context()`, never `api.llm.client` directly. `resolve_entity` is called from `discover.py`
   for every candidate — `candidate_entity_hint` threading from `ExtractedClaim` was **not** used:
   discovery resolves entities from search-result URLs before any extraction happens, not from
   hints inside already-extracted claims, so that particular wiring never had a caller in v1 (noted,
   not a regression — nothing currently produces an `ExtractedClaim` with a hint that needs a
   *second*, later entity-resolution pass).
7. Phase 14's quota/cost math should use Phase 03's measured 75% path-guess hit rate (not Phase
   01's 82%, which used a different, looser matching method — see Recent Activities) when
   re-deriving expected search volume and the Exa allowance's real headroom. It should also set
   `exa_daily_credit_cap_usd`/`exa_global_daily_credit_cap_usd` (both `None`/unenforced today) from
   real measured credits-per-run, per Phase 04's Recent Activities entry. Phase 07's maturity
   thresholds (`api.resolve.maturity`, all named module-level constants) are explicitly first-pass
   guesses too, per that phase's own doc — same Phase 14 calibration pass should retune them
   against the benchmark set's hand-labelled entities.
8. ~~Phase 06's `claims.extractor_version`...~~ **Done in Phase 06**: `extractor.extractor_version_for`
   composes `f"{prompt_version}-{model}"` exactly as Phase 05 settled — see Phase 06's Recent
   Activities entry.
9. Phase 06's two carried-forward open decisions still need real data before deciding, both
   explicitly deferred to [Phase 14](execution_phases/phase-14-benchmark-calibration.md) by that
   phase's own doc: **(a) minimum quote length** — no floor imposed yet; the phase doc's own risk is
   real short factual quotes (e.g. `"$5/user/month"`) getting excluded by an aggressive floor, so
   this needs the real-page ambiguity-drop rate, not the synthetic corpus's 1-in-17 (see Phase 06's
   Recent Activities); **(b) SERP-snippet claims** (carried from Phase 04) — whether a search-result
   snippet is valid source text for span binding at all (proposal: yes, `retrieval_reason=
   "serp_snippet"`, grade C) is still unresolved; Phase 06's `extract_claims` takes whatever
   `Source.extracted_text` it's given and has no opinion on where that text came from, so this
   decision doesn't block it either way — it matters once Phase 10 decides whether to route SERP
   snippets through extraction at all.
10. Phase 07's two open decisions (its own phase doc, "Open decisions"), both explicitly deferred:
   **(a) cross-run entity persistence / maturity staleness** — entities are global
   (`entity_key` unique table-wide), so a second run in the same category reuses them, but
   `store.upsert_entity` always overwrites `maturity` with the freshest classification rather than
   checking staleness; proposal (unconfirmed) is to refresh only when the newest signal is >30 days
   old. **(b) whether `ph:` entities belong in reports at all** — a Product Hunt slug with no other
   artifact is a pre-launch announcement, not a shippable competitor; current behaviour includes
   them, tiered via the same maturity rules as everything else (no `ph:`-specific carve-out). Both
   wait on [Phase 14](execution_phases/phase-14-benchmark-calibration.md) showing real category-
   overlap/pre-launch-relevance data.
11. Phase 08's two open decisions (its own phase doc, "Open decisions"), both explicitly deferred:
   **(a) should the contradiction penalty apply to the winner, the loser, or both?** — currently the
   winner only (the loser is superseded and not scored for display); decide when
   [Phase 13](execution_phases/phase-13-frontend.md) settles how contradictions render, since the
   answer depends on what the UI actually shows. **(b) cross-run contradiction detection** — today's
   query is scoped to one `run_id`; detecting that a run disagrees with a *previous* run on the same
   (global) entity is out of scope for v1, noted but not built. Neither blocks
   [Phase 11](execution_phases/phase-11-synthesis-report-assembly.md), which only needs the current,
   per-run behaviour.
12. ~~Phase 09 needs DB-backed verification before it can be called closed~~ **Done**: Docker came
   up mid-phase; `make check` against real Postgres passed 575/575 with 97.35% coverage overall
   (`src/api/planner/` 93–100% per module) — see Recent Activities.
13. Phase 09's own two open decisions (its own phase doc, "Open decisions"), both explicitly
   deferred: **(a) should the planner see Phase 04's remaining search-budget state?** — not
   wired; `plan_stage1` takes `run_budget_weight` as a plain parameter with no visibility into
   Exa's remaining daily/monthly allowance. Proposal (per the phase doc): pass remaining budget
   as a plan input in a v1.1 pass, not v1. **(b) venue selection for `mine_community`** — the
   planner currently selects venues directly (`hn`/`github`/`stackexchange`, per D5's backbone);
   the phase doc's alternative — planner expresses intent, handler maps intent to whatever
   venues are actually available — is deferred to a later pass now that the venue set is fixed
   (D5 closed Reddit as a source).

14. ~~Phase 10 needs DB-backed and live verification before it can be called closed~~ **Done**:
   `make check` green (see Recent Activities), pipeline e2e test proven against real Postgres, and
   `python -m api.cli run` proven against the real internet.
15. Phase 10's own two open decisions (its own phase doc, "Open decisions"), both explicitly
   deferred: **(a) competitor ranking signals** — currently simple and deterministic
   (`api.tasks.discover._RankedEntity.score`: frequency across result sets, search rank, non-hobby
   maturity); if Phase 14 shows recall limited by ranking the wrong candidates into the profile
   budget, add signals but keep it deterministic (a model ranking competitors reintroduces exactly
   the hallucination surface artifact verification exists to close). **(b) should `mine_community`
   run before profiling?** — not built; the fallback/planner-emitted DAG has no dependency edge
   between `discover_competitors` and `mine_community`, so in practice they run concurrently with no
   ordering guarantee either way. Measuring whether community-sourced discovery would add unique
   entities (and is therefore worth a serialisation point) is left to Phase 14.
16. Phase 10 also surfaced its own new open items, none blocking Phase 11: **(a)** `trend_signals`
   persists no claims (no `ClaimAttribute` slot exists for trend/volume data) — Phase 11 needs
   somewhere in the report to put this, or it stays collected-but-unused; **(b)** `mine_community`
   does not attempt to attach a complaint/request claim to an already-discovered competitor entity
   when the mined text is actually about one — every such claim currently lands on the synthetic
   `category:<run_id>` bookkeeping entity regardless; **(c)** discovery's seed strategies are
   narrower than the phase doc's own list (general search + GitHub `awesome-` repos only; no
   AlternativeTo, no package-registry search) — see Recent Activities for why neither had a retriever
   to call; **(d)** the GitHub Starring-endpoint gap (open since Phase 01) was directly
   observed degrading a real run's `oss.stars_90d_delta` to an approximation rather than an exact
   figure — **resolved 2026-08-07 as a permanent vendor restriction, not a config fix** (GitHub
   locked `/stargazers` to repo admins/collaborators on 2026-06-30; fine-grained PATs unsupported
   for it) — see Next Steps item 2.
17. Phase 11's own new open items, none blocking Phase 12 (all carried from its Recent Activities
    entry or phase doc's "Open decisions"): **(a)** `api.tasks.community`'s thread-breadth
    approximation — a `complaint.*`/`request.*` claim has no surviving link to which real thread/
    issue it came from (Phase 10 bundles up to `max_community_threads` into one synthetic source),
    so `api.synth.findings` uses distinct `source_id` as the proxy and
    `api.evidence.promotion.evaluate_github_theme` (reaction-weighted) has **no real caller in
    v1**; fixing properly means persisting real per-claim thread/issue identity in Phase 10's
    handler, before Phase 14's promotion calibration can trust breadth figures. **(b)** two frozen
    Phase 00 `Report` leaf fields were widened this phase (`CompetitorEntry.maturity` →
    `Maturity | None`, `ContradictionValue.v` → `float | str`) — logged, not hidden; Phase 13's
    frontend should expect and render both the nullable maturity and the string contradiction
    values. **(c)** the phase doc's own Open Decision #1 — should low-confidence findings be shown
    at all? — still open, proposal unchanged: show with visual de-emphasis in Phase 13, don't
    filter server-side. **(d)** the phase doc's Open Decision #2 — `feature_gap` findings are
    inferred from *absence* of a `feature.<slug>.present` claim — partially settled in this
    phase's own prompts, which phrase gaps as "not found among {n} reviewed competitors" (the
    findings template) and "not found in the reviewed sources" (the gaps prompt), but the
    decision about whether that's sound enough to show as a finding is Phase 13/14's.
    **(e)** Phase 10's own `trend_signals` open item (item 16a) is now confirmed collected-but-
    unused: the frozen §2 `Report` contract has no section for trend/volume data and Phase 11
    added none — stays a v1 gap unless Phase 14 argues for a contract change.
18. **Begin Phase 13 (Frontend & Drill-Down UI) or Phase 14 (Benchmark Harness & Calibration) —
    either can start next.** Phase 13 depends on Phase 12 (now done); Phase 14 depends only on
    Phase 11 and is independent of 12/13, so it does not need to wait. Phase 12's own open items,
    none blocking either: **(a)** the phase doc's Open Decision #1 (anonymous trial runs) is
    **unresolved, deliberately** — masterplan §12.8 rejects it because search credits can't be
    quota'd per-anonymous-caller; revisit once Phase 14 has a real per-run cost figure (if a run is
    genuinely ~$0.04, one free anonymous run is a cheap acquisition cost). **(b)** no kill-switch
    admin HTTP endpoint exists (the phase doc's Endpoints table names none) — `api.web.killswitch.
    trip`/`reset` are directly callable; Phase 15 may want a thin ops script or an admin-gated route
    around them. **(c)** `PATCH /runs/{id}` (disambiguation resolution) has three tests, not an
    exhaustive per-field matrix — a deliberate scope call, same spirit as other phases' accepted
    coverage floors. **(d)** all quota knobs (`runs_per_user_per_day`, `global_runs_per_day`,
    `max_concurrent_runs`) are still `TBD`/`None` (unenforced) — Phase 14's job, per the phase doc's
    own scope.
19. **Begin Phase 14 (Benchmark Harness & Calibration).** Depends only on Phase 11 and was always
    unblocked; Phase 13 (now done) was the other independent option. Phase 13's own open items, none
    blocking Phase 14: **(a)** `PlanChecklist` matches streamed `task.*` events to plan nodes by
    `kind` only — the public SSE contract carries no `node_key` (see Recent Activities) — correct for
    a checklist's purposes but worth knowing before building anything that needs an exact
    task-to-node mapping. **(b)** `DisambiguationChips` renders an editable best-guess pill, not the
    masterplan §3 mockup's closed alternative set, because `select_disambiguation_fields` reports
    only which fields are ambiguous, never candidate values (`segment`/`geography` are free text, not
    enums) — a real API gap, not a frontend shortcut; closing it would mean the interpreter emitting
    real alternatives, which is Phase 09/14 territory, not Phase 13's. **(c)** the Phase 12 backend
    extension made this session (`grade`/`confidence`/`confidence_inputs`/`source_fetched_at`/"other
    claims", plus the new `GET /runs/{id}/findings/{id}`) has no Docker in this environment to verify
    against live Postgres — re-run `make integration` for real before trusting it the way every prior
    phase's DB-backed work was trusted. **(d)** the E2E suite's authenticated flow
    (`tests/e2e/live-run.spec.ts`) exercises a planted Supabase session, not a real Google/GitHub OAuth
    round trip — re-verify against a real Supabase project before Phase 15 deploys anything behind it.
    **(e)** only Chromium is installed in this sandbox (no root for WebKit's system deps) —
    `mobile-chrome` stands in for `mobile-safari` in `playwright.config.ts`; swap back wherever
    `--with-deps` can run.

- **Implemented Phase 14**: `bench/` — `loader.py` (`BenchmarkQuery`/`GroundTruth`/`GroundTruthFact`
  Pydantic models over `bench/queries/*.yaml`; `STALENESS_DAYS = 60` enforced mechanically —
  `load_tuning_queries`/`load_held_out_queries` raise `StaleGroundTruthError` rather than silently
  scoring a stale fact; `load_held_out_queries` additionally requires `confirm=True` and logs a loud
  warning on every call, the mechanical half of masterplan §10's "touched once, at the end"
  discipline), `metrics.py` (every masterplan §10 metric as a pure function over a `Report`/
  `GroundTruth` — `competitor_recall`, `precision_proxy`, `fact_accuracy` (numeric facts within
  `NUMERIC_FACT_TOLERANCE_USD=$1`, everything else exact/case-insensitive), `sentence_binding_rate`,
  `contradiction_fired`, `synthesis_omitted_sections`, `extraction_drop_breakdown`,
  `planner_fallback_rate`, `synthesis_rejection_rate`, `cost_summary`/`latency_summary`/
  `cache_hit_rate_summary`/`coverage_summary` — two are honestly-scoped proxies, documented as such in
  the module docstring rather than silently approximated: `sentence_binding_rate` checks the
  *structural* invariant `Report`'s own aggregate `claim_ids`/`addresses_finding_ids` fields can prove,
  not a true per-sentence count `api.synth.bind` computes internally and never returns;
  `cache_hit_rate_summary` reports only the *source* (fetch) cache hit rate, since search/extraction
  cache hits are not instrumented anywhere in the pipeline today), `runner.py` (`run_and_score` drives
  `api.cli.run_query` in-process and scores the result; `write_results` persists a dated JSON snapshot
  per query; `--cached-only` swaps in an `httpx.MockTransport` that raises on any request it sees at
  all — needs no change anywhere in `src/api/`, since every layer's own cache is already checked before
  a real HTTP call, so a genuinely warm run never reaches the transport; `--export-cache-seed` /
  `export_cache_seed` shells out to `pg_dump --data-only` for the eight cache-relevant tables),
  `regression.py` (`check_regression` — the phase doc's four CI failure conditions: sentence binding
  <100%, recall dropping >10 points from `bench/results/baseline.json`, cost rising >50%, the trap
  query's contradiction detector going quiet — pure comparison over two `QueryScore` sets, no I/O
  beyond what's already on disk), `bench/queries/q01.yaml`–`q10.yaml` (six tuning / four held-out, 3
  easy / 4 medium / 3 hard-and-thin, every fact hand-verified via real web research on 2026-08-08 —
  not recalled from training data — with dated `verified_on`/`source` fields).
  `.github/workflows/bench.yml` (nightly, ephemeral Postgres seeded from a committed cache dump,
  `--tuning --cached-only` then `python -m bench.regression`, zero real spend — `continue-on-error`
  on both, see Blockers). `make check`: **792 tests** (up from 730), **96.05% coverage overall**,
  stable across two consecutive full runs; `bench/loader.py` 100%, `bench/metrics.py` 99%,
  `bench/regression.py`'s non-CLI logic fully covered, `bench/runner.py` excluded from the coverage
  gate (drives real vendors/Postgres end to end, same treatment as `api.cli`/`api.config`).
  **All ten queries were actually run against real vendors this session, not simulated** — full
  numbers, every real finding, and every calibration/quota decision: `docs/benchmark.md`/
  `docs/tuning.md`. Highlights (full detail in those two docs, not duplicated here):
  - **A real gap found and fixed at the Phase 14 boundary, the same "surgical extraction" pattern
    every prior phase has used when a real caller needs something an earlier phase's module didn't
    expose**: `api.cli.cmd_run` only ever printed — no return value, and `is_benchmark`/`is_public`
    (real `runs` columns since Phase 00, read by Phase 12's own `GET /reports/benchmark`) were never
    set anywhere in the codebase. Extracted the existing body into `run_query(pool, http, settings,
    query, ...) -> RunOutcome` (`run_id`, `report`, `cost_usd`/`llm_cost_usd`/`search_cost_usd`,
    `coverage`, `duration_s`, `used_fallback`, `stats`); `cmd_run` is now a thin wrapper that prints
    from it, behavior-unchanged for every existing caller (`tests/integration/test_pipeline_e2e.py`
    still passes untouched). `create_run` gained `is_benchmark: bool = False`; `is_public` is
    deliberately **never** auto-set — publishing a benchmark report to the public homepage stays a
    separate, deliberate step, not a side effect of running one. New
    `tests/integration/test_cli_run_query.py` exercises the *whole* pipeline (interpret → plan →
    execute → assemble), unlike `test_pipeline_e2e.py`'s deliberately-narrower deterministic-fallback
    walking skeleton.
  - **`bench/` lives at the repo root, a sibling of `src/`, not installed into the `api` wheel** —
    mirrors `spikes/`'s own precedent from Phase 01. Needed `pythonpath = ["."]` added to
    `[tool.pytest.ini_options]` (a genuine gap: nothing had ever needed to import a repo-root
    non-`src` package from `tests/` before this — `spikes/` itself has never been imported by any
    test, only referenced in module docstrings) and `--cov=bench` / `bench` added to
    `[tool.coverage.run] source`, `bench/runner.py` added to `omit` alongside `api.cli`/`api.config`.
  - **The single biggest real finding: 0% recall against household-name ground truth on every one of
    the six tuning queries, traced to three independent, confirmed causes, not one bug** — (1) the
    planner incorrectly engaged GitHub OSS discovery for plainly-mainstream categories on both of this
    benchmark's own "GitHub-should-be-skipped" role queries (q01 "project management tool", q03
    "video conferencing software" — spawning `oss_profile` against curated `awesome-*` list repos and
    fan-fic-adjacent side projects, never real competitors); (2) discovery, when it worked normally,
    consistently surfaced real, artifact-verified, but long-tail/indie products over the well-known
    market leaders (q04's expense tracker query found `mozey.co`/`centsense.app`/`fwdtools.com`/
    `flexpro.app`/`vuuv.co`/`keepr.co.uk`, never Expensify/Wave/FreshBooks — precision stayed 100%
    throughout, so this is a recall problem, not hallucination); (3) a structural gap in
    `api.synth.assemble.build_competitors`'s all-or-nothing pricing-triple requirement means a
    genuinely, permanently free OSS product (q08's `docusaurus.io`, real `pricing.free_tier=true`
    claim, zero paid tiers to report) can **never** appear in `report.competitors` at all — the closed
    `pricing.model` vocabulary (`seat|usage|flat|freemium`) has no honest value for "there is no paid
    tier." All three are real, owning-phase findings (Phase 09/04 for #1/#2, Phase 00/11's `Report`
    contract for #3) — logged per this phase's own explicit scope ("changing product behaviour beyond
    tuning constants... a design flaw is a fix in the owning phase"), not patched here.
  - **The trap query fired the contradiction detector, but not on the researched trap.** `q07.yaml`
    documents a real, dated Help Scout pricing-model reversal (contacts-based usage pricing in 2025,
    reverted to per-seat in 2026, full citations in the query file) specifically so the detector would
    have something genuine to catch — `helpscout.com` was never discovered by the live run (cause #2
    above), so the fired contradiction (`contradiction_fired=true`, satisfying the raw exit-criterion
    boolean) was unrelated: a false positive on `helpspot.com`'s `product.integrations` (`"REST API"`
    vs. `"Office365"` — both true simultaneously, not a real disagreement). The same shape recurred on
    other tuning runs (`klaviyo.com`: three integrations flagged mutually exclusive;
    `wapmini.in`: platform values across eight sources). Real finding:
    `api.evidence.contradictions`'s `GROUP BY attribute HAVING count(distinct value) > 1` treats every
    closed-vocabulary attribute as single-valued, but `product.integrations`/`product.platforms` are
    legitimately multi-valued — a genuine Phase 08 design gap the benchmark found exactly as the phase
    doc hoped it would ("without \[a trap\] there is no evidence the contradiction detector ever fires
    rather than silently never triggering" — it fires, just not selectively enough).
  - **Synthesis (MVP/feature-gaps/risks) never fired on any of the six tuning runs** — traced, not
    guessed at: every `complaint.*`/`request.*` theme observed had `support_count` 1-4, never near
    `api.evidence.promotion.COMMENT_SUPPORT_THRESHOLD=5`. Root cause found in
    `api.tasks.community.MineCommunityHandler`'s own `per_call_limit = max_community_threads //
    (keywords * venues)`: real plans used 6-18 keyword/venue pairs against the old default of 10,
    flooring `per_call_limit` to exactly 1 in every single run observed. `MAX_COMMUNITY_THREADS`
    doubled to 20 (`docs/tuning.md`) partially relieves this; a full fix needs a per-pair floor or
    fewer keyword variants, a Phase 09/10 design note, not something one flat cap fully solves.
  - **What held up under real, messy, live data**: sentence binding 100% on all ten runs, no
    exceptions — the one hard pass/fail metric, and `api.synth.bind`'s "drop, don't fabricate"
    discipline never broke once. Precision 100% across ten runs — masterplan Rule 2 (verifiable
    artifact required) structurally prevented every hallucinated competitor, even while recall
    struggled. `coverage.score` read `0.00` on every run, confirming (not newly discovering) Phase
    10's own finding at real production scale: every `web:` entity across all ten runs landed on
    `insufficient_signal`, since no domain-age/install-count source exists for that scheme yet.
  - **Calibration: four constant groups reviewed, all four kept unchanged, each for a distinct,
    evidence-based reason** (`docs/tuning.md` has the full reasoning) — `evidence/confidence.py`'s
    formula constants are masterplan-specified, not guesses, and the six runs produced almost no
    matched-fact data to responsibly override them against; `evidence/promotion.py`'s
    `COMMENT_SUPPORT_THRESHOLD`/`COMMENT_MIN_DISTINCT_THREADS` are likewise masterplan-specified, and
    the real bottleneck (traced above) was `MAX_COMMUNITY_THREADS`, not these; `GITHUB_REACTION_
    THRESHOLD` is confirmed **still untunable** — `evaluate_github_theme` has zero real callers,
    exactly as Phase 11's own tracker entry already found; `resolve/maturity.py`'s five thresholds and
    `synth/cluster.py`'s `DEFAULT_SIMILARITY_THRESHOLD` both had no real per-entity signal or enough
    clustering data (18 complaint/request claims total across all six runs, mostly singletons) to
    calibrate against. "Kept, and here's why" counted as a real calibration decision throughout, not a
    skipped one.
  - **Every masterplan §8.2 quota knob derived from the six live runs and set for real** (full
    arithmetic in `docs/tuning.md`) — `RUN_BUDGET_WEIGHT`: 40 → **70** (p95 *wanted* plan weight
    across the six runs was 47, × 1.5 headroom; the old 40 was observed directly causing q01's
    zero-competitor report by starving real `profile_product`/`extract_pricing` tasks after OSS
    profiling ate the budget first) — this is also a `DEFAULT_RUN_BUDGET_WEIGHT` change in
    `api.planner.registry`, not just a `.env` value, since that constant is the actual fallback used
    whenever `Settings.run_budget_weight` is unset; `RUN_BUDGET_USD`: unset → **0.25** (p95 cost
    $0.0777 × 3); `RUN_TIMEOUT_S`: unset → **640** (p95 duration 317s × 2 — confirmed to have zero
    consumers anywhere in the codebase; the value is set per the masterplan §8.2 checklist, the wiring
    gap is a carried-forward Phase 15 item); `MAX_COMPETITORS_PROFILED`/`MAX_PAGES_PER_ENTITY`: kept
    at 8/4 (no sweep performed, no signal pointed at either); `MAX_COMMUNITY_THREADS`: 10 → **20**
    (`DEFAULT_MAX_COMMUNITY_THREADS` in `api.tasks.context`, same "real fallback, not just `.env`"
    treatment — the traced per-pair-floor bug above); `GLOBAL_RUNS_PER_DAY`: unset → **4** (`(Exa
    $10/mo ÷ 30) ÷ p95 search-$/run ($0.070)` ≈ 4.76, floored per the phase doc's own p95-not-median
    rule); `RUNS_PER_USER_PER_DAY`/`MAX_CONCURRENT_RUNS`: unset → **3/2** (judgement calls, not
    derivable from six queries alone, documented as such); `EXA_DAILY_CREDIT_CAP_USD`/
    `EXA_GLOBAL_DAILY_CREDIT_CAP_USD`: unset → **0.33/0.33** (`$10/mo ÷ 30`, the two collapsing to one
    system-wide check by Phase 04's own design).
  - **A real, if narrow, test-infrastructure interaction found while verifying `make check` after
    setting the new quota values for real**: `api.web.quota.try_create_run`'s admission check counts
    `runs` rows by wall-clock `started_at > now() - interval '1 day'`, not scoped to any one test —
    enforcing a small real `GLOBAL_RUNS_PER_DAY` in this repo's own local `.env` (read by every
    `Settings()` call, including test helpers that don't override it) made
    `tests/integration/test_quota.py`/`test_api.py` start tripping the kill switch on rows *other*
    tests in the same session had already inserted into the shared, long-lived local `ai_pi_test`
    Postgres — never exercised before since these knobs were always `None`. Not a bug in
    `try_create_run` (working exactly as designed) or in those tests; resolved by leaving
    `RUNS_PER_USER_PER_DAY`/`GLOBAL_RUNS_PER_DAY`/`MAX_CONCURRENT_RUNS` commented out in this repo's
    own local `.env` specifically (documented inline there and in `docs/tuning.md`) while shipping
    the derived values in `.env.example` for a real deployment to uncomment — `.env` is gitignored and
    CI never reads it (`ci.yml` sets its own placeholder env vars directly), so this is a purely local
    convenience, not a scope reduction on the deliverable.
  - **The held-out run hit the newly-derived `EXA_DAILY_CREDIT_CAP_USD` for real, mid-run, on its
    first attempt** — the six tuning runs plus the first held-out attempt spent more than $0.33 of Exa
    credit within the same real calendar day (both batches run hours apart in one working session, not
    across a genuine day boundary), and search began degrading (`search.degraded... credit allowance
    exhausted`) partway through q05. Killed before any held-out result was scored or written (the
    runner only persists results after a full batch completes, so nothing partial or misleading
    landed on disk); the cap was temporarily unset for a clean held-out run and restored immediately
    after, documented in `docs/tuning.md` rather than silently worked around — a genuine demonstration
    that the derived cap does exactly what it's for.
  - **The re-run held-out set reproduced the tuning set's own findings independently, on data never
    looked at until this run — not overfitting, the same real gap** (masterplan §10's own held-out
    discipline: "if the held-out numbers are much worse... that is reported, not tuned away"). 3 of 4
    held-out queries (q03, q05, q06) independently reproduced 0% recall against real ground truth,
    same long-tail-over-household-name pattern; **all four held-out contradictions that fired were the
    same `product.integrations`/`product.platforms` false positive** already found on the tuning set
    (`sentry.io`: ten platforms flagged mutually exclusive; similarly `shakebug.com`/`dialpad.com`/
    `dune.com`/`kanorio.com`) — across the full ten-query benchmark, **zero of eight fired
    contradictions were the researched Help Scout trap**, generalizing the Phase 08 finding from "seen
    once" to "seen on every run that fired one." **q10 ("MEV monitoring for solo Ethereum validators,"
    the thin-category role query) is this benchmark's one genuinely clean result**: the real run
    correctly returned zero competitors for a category whose ground truth was deliberately built
    empty, with zero `known_absent` false positives either — masterplan Rule 2 holding under a
    genuinely adversarial "there's almost nothing real here" input, exactly what the query exists to
    test, and the only run in all ten with a nonzero `coverage.score` (0.041, worth a follow-up look,
    not chased further this phase).
  - **A genuine, unresolved architectural gap found while verifying `bench.yml`'s own zero-spend
    promise, confirmed empirically, not just read from the code**: `python -m bench.runner --tuning
    --cached-only`, re-run against the very same Postgres the live tuning benchmark had just
    populated, still fails at least one task on five of six queries.
    `api.retrieval.robots.RobotsCache` keeps parsed `robots.txt` results in a plain in-memory dict,
    never persisted to Postgres — a fresh process starts with an empty robots cache regardless of how
    warm the `sources`/`search_cache`/`extraction_cache`/`llm_response_cache` tables are.
    `api.sources.hn.HNRetriever` and GitHub's Search API have **no caching layer at all** — masterplan
    §9 names exactly three cache types and none cover domain retrievers. This is real Phase 03/04
    infrastructure work (new cache tables, new read/write paths per retriever), not a Phase 14
    "tuning constant" — `bench.yml` ships anyway, `continue-on-error: true` on the two affected steps
    with a top-of-file comment explaining why, rather than either lying about being green or
    withholding the workflow the phase doc's own exit criteria ask for. A second, distinct,
    not-yet-diagnosed bug surfaced in the same pass: `api.resolve.store.merge_alias` raised a real
    Postgres FK violation trying to merge an entity that still had `claims` referencing it from an
    earlier run — logged for Phase 07, not chased down this phase. Full detail:
    `docs/tuning.md`'s §6.
  - Full numbers for both splits, every finding in complete detail, and the methodology notes:
    [`docs/benchmark.md`](benchmark.md). Full design/scope:
    [`docs/execution_phases/phase-14-benchmark-calibration.md`](execution_phases/phase-14-benchmark-calibration.md).
  - **The phase doc's own exit criteria, walked explicitly rather than left implicit** — 13 of 18 met
    cleanly, 4 met only partially (each a real, traced finding above, not a shortcut), 1 not met at
    all (a genuine, logged infrastructure gap):
    - ✅ Ten queries, matching difficulty and shape composition rules (3 easy/4 medium/3 hard-thin;
      consumer-no-GitHub, dev-tools-GitHub, OSS-dominated, thin-category roles all assigned).
    - ✅ At least one trap query with a recent pricing change (q07, the real Help Scout reversal).
    - ✅ All ground truth hand-verified and date-stamped (`verified_on: 2026-08-08`, real web research,
      not recalled from training data — including a real correction to the masterplan's own worked
      example, Ramp's freelancer ineligibility).
    - ✅ Six tuning / four held-out split enforced mechanically (`load_held_out_queries(confirm=True)`
      plus a loud log line on every call, not just a convention).
    - ❌ **Runner executes from cache at zero spend — not met.** `RobotsCache`/domain-retriever
      persistence gap, confirmed empirically; `bench.yml` ships anyway with `continue-on-error`,
      `docs/tuning.md` §6.
    - ✅ All metrics computed and recorded in `docs/benchmark.md`, dated.
    - ✅ **Sentence binding rate is 100%** — on all ten runs, no exceptions.
    - ⚠️ **Contradiction detector fires on the trap query — met in letter, not in spirit.** It fired
      (raw boolean true) but never once on the researched Help Scout trap across either split — every
      fired contradiction traced to the `product.integrations`/`product.platforms` false-positive
      class instead (Phase 08 finding, `docs/benchmark.md`).
    - ✅ Thin-category query returns few/zero competitors, not invented ones (q10: zero competitors,
      zero `known_absent` false positives — this benchmark's one genuinely clean result).
    - ❌ **Consumer query correctly skips GitHub; dev-tools query uses it — the consumer half failed
      twice.** q01 and q03 (both consumer-role queries) both incorrectly engaged GitHub OSS discovery;
      q05 (dev-tools role) correctly used it. 1 for 2, not 2 for 2 — a real Phase 09 finding.
    - ✅ All four constant groups calibrated, each with a recorded justification (`docs/tuning.md` §§1-4
      — three "kept, here's why," one "untunable this phase and here's why").
    - ✅ **Every `TBD` in masterplan §8.2 replaced with a derived value.**
    - ✅ `GLOBAL_RUNS_PER_DAY` derived from p95 search *count* (10 searches/run, measured directly from
      `search_credit_usage`), not guessed — `docs/tuning.md` §5.
    - ✅ Held-out set run once, after tuning; results reported honestly, including that 3 of 4 held-out
      queries independently reproduced the tuning set's own 0%-recall finding.
    - ⚠️ **CI regression job runs nightly with the four failure conditions — ships, but not fully
      green.** `bench.yml`/`bench.regression` exist and are correct; the underlying cached replay they
      depend on has the same zero-spend gap above, so `continue-on-error` keeps the job from blocking
      on a known, already-diagnosed limitation rather than pretending it passes.
    - ✅ Masterplan §14 open items #1 and #2 closed (see "Open Items Carried From the Masterplan" table
      below).
    Per this project's own established convention (Phase 09/10/11 all shipped with logged, non-blocking
    gaps rather than silently claiming completion): every unmet or partially-met criterion above is a
    real, traced, owning-phase finding — not a shortcut taken to check a box.
20. **Begin Phase 15 (Deployment, Observability & Cost Control).** Its dependency (12+13+14) is now
    satisfied. Worth reading before starting: the three real findings in this entry's own Blockers
    section (recall, the trap's false-positive contradiction, synthesis never firing) are not
    deployment blockers per se, but they mean the report quality a Phase 15 deployment actually ships
    is measurably weaker than the masterplan's own aspirational examples — worth setting expectations
    with whoever the audience is before it's live. `RUN_TIMEOUT_S` is set but has no consumer; Phase 15
    is a natural place to wire it into the executor's per-run timeout if that's judged worth doing.
    `bench/fixtures/cache_seed.sql` is the seed CI's nightly `bench.yml` job restores — if Phase 15
    changes the schema, that seed needs regenerating (`python -m bench.runner --export-cache-seed
    --pg-dump-via-docker-compose`) or `bench.yml` will fail to restore it.

### 2026-08-10 - Phase 15 pre-work: persistent replay caching

- Added migration `0011` with `retriever_cache` and `robots_cache`.
- Added persistent 24-hour caching for robots decisions, HN, GitHub search/metadata/star velocity,
  Stack Exchange, and Wikimedia pageviews; unavailable responses are cached where safe.
- Added regression coverage for fresh-process HN, GitHub, and robots cache reuse; focused suites pass.
- Added both tables to cache-seed export. q08 cached-only now completes 19/19 tasks at zero cost and
  zero network. The full six-query replay remains non-blocking until historical HN/profile/planner
  cache fixtures are refreshed after prompt-version changes; `continue-on-error` remains correct.

## Open Items Carried From the Masterplan

| # | Item | Closes in |
|---|---|---|
| 1 | All quota and budget values | **Closed in [Phase 14](execution_phases/phase-14-benchmark-calibration.md): every masterplan §8.2 knob derived from six live benchmark runs and set — see this entry's own Recent Activities and `docs/tuning.md`.** |
| 2 | The ten benchmark queries + hand-verified ground truth | **Closed in [Phase 14](execution_phases/phase-14-benchmark-calibration.md): `bench/queries/q01.yaml`–`q10.yaml`, every fact hand-verified via real web research on 2026-08-08, dated `verified_on`/`source` fields.** |
| 3 | Whether Playwright is needed at all | **Closed in [Phase 01](execution_phases/phase-01-dependency-validation-spike.md): no — deferred behind a feature flag. Static crawl hit rate (88%) clears the masterplan's 80% bar; ships only if Phase 14 recall proves JS-rendering-limited.** |
