# Execution Tracker

Last Updated: 2026-08-06

## Current Status

- **Phase**: Phase 03 complete — `src/api/retrieval/` (`fetch_source(url) -> Source`) is built and
  green: URL canonicalisation (idempotence proven by property test), conditional-request caching
  (7d positive / 24h negative TTL), robots.txt + hardcoded no-crawl enforcement, per-host
  politeness, trafilatura extraction with a single canonical normalisation pass, and path-guessing
  with its own positive/negative cache and attempt log. **Real measured path-guess hit rate: 75%
  (30/40)** against the 40-domain corpus — see `docs/external_apis.md`'s new "Fetch &
  path-guessing (Phase 03)" section for the full breakdown and why it differs from Phase 01's 82%.
  `make check` green: 147 tests, 95.8% coverage overall, every `retrieval/` module ≥ 90%.
- **Focus**: Ready to begin Phase 04 — Search & Domain Retrievers
- **Blockers**: None for Phase 04. Two open, non-blocking credential items carried forward:
  Product Hunt developer token (not started) and Reddit API application (not yet submitted —
  see Next Steps). GitHub's fine-grained PAT also needs a permission upgrade before Phase 04/07
  build on star-velocity (see Recent Activities).

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
  - **Reddit**: **not yet applied for in this pass** — flagging explicitly since the phase doc's
    exit criterion is "applied for," which is not yet true. Community mining ships on HN Algolia +
    GitHub + Stack Exchange regardless (D5); Reddit remains a coverage-gap item until submitted.
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

## Ongoing Work

- [x] Phase 00 — Foundation, Contracts & CI (complete; `make check` green including all
      Postgres-backed integration tests against live Postgres)
- [x] Phase 01 — Dependency Validation Spike (complete; two non-blocking credential items open —
      see Current Status)
- [x] Phase 02 — Executor Core (complete; chaos suite green, flake-free at 30x local repeat,
      50x nightly in CI)
- [x] Phase 03 — Fetch, Text Extraction & Source Cache (complete; path-guess hit rate measured
      at 75%, real number carried into Phase 14's quota math — see Recent Activities)
- [ ] Phase 04 — Search & Domain Retrievers — **up next**

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

**under $5/month fixed**, **~$0.012/run measured for LLM** (Phase 01, 60-page extrapolation —
better than the masterplan's $0.03 estimate) + **~$0.056/run for search** (Exa, ~8 queries/run at
$0.007 flat), both well inside the $10/mo Exa allowance (~179 runs/month ceiling) and the
essentially-free LLM budget. Path-guessing hit rate came in at 82% (Phase 01, real 40-page corpus)
— above the masterplan's assumption, so the ~4× search-volume-inflation risk did not materialize.

## Next Steps

1. **Submit the Reddit application** — still not done as of the Phase 01 close-out; 2–4 week
   manual approval once submitted, and it is not blocking anything else. Product Hunt (developer
   token, minutes) is similarly open but non-blocking.
2. **Upgrade the GitHub PAT** before Phase 04/07 build on star-velocity — the current fine-grained
   PAT's default public-read scope returns 403 on the Starring endpoint (REST and GraphQL both).
   Either switch to a classic PAT or add an explicit Starring permission to the fine-grained one.
3. Begin [Phase 04](execution_phases/phase-04-search-domain-retrievers.md) — search and domain
   retrievers, now that both the source cache (`fetch_source`, Phase 03) and every external
   dependency it schedules work against (Phase 01) are built and de-risked. Phase 04's unified
   retriever interface should call `api.retrieval.fetch_source` directly for anything URL-shaped
   rather than re-implementing fetch/cache/robots logic — that's the whole point of this layer.
4. Phase 00's contracts (`src/api/models/`) remain frozen; Phase 02 and Phase 03 both added their
   own new types instead of touching them (`api.executor.protocol`, `api.models.source.Source`) —
   see Recent Activities. Both extended the *schema* via migration (`0002_executor_core`,
   `0003_fetch_source_cache`), logged there per the phase doc's rule that schema changes need a
   tracker note.
5. When Phase 10 builds real task handlers, it must adapt `api.models.plan.Plan` into the
   executor's `ExecutionPlan`/`TaskSpec` at the boundary (kind values become `TaskKind.value`
   strings) — the two are deliberately not the same type; see Recent Activities.
6. Phase 14's quota/cost math should use Phase 03's measured 75% path-guess hit rate (not Phase
   01's 82%, which used a different, looser matching method — see Recent Activities) when
   re-deriving expected search volume and the Exa allowance's real headroom.

## Open Items Carried From the Masterplan

| # | Item | Closes in |
|---|---|---|
| 1 | All quota and budget values | [Phase 14](execution_phases/phase-14-benchmark-calibration.md) |
| 2 | The ten benchmark queries + hand-verified ground truth | [Phase 14](execution_phases/phase-14-benchmark-calibration.md) |
| 3 | Whether Playwright is needed at all | **Closed in [Phase 01](execution_phases/phase-01-dependency-validation-spike.md): no — deferred behind a feature flag. Static crawl hit rate (88%) clears the masterplan's 80% bar; ships only if Phase 14 recall proves JS-rendering-limited.** |
