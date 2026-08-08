# Execution Tracker

Last Updated: 2026-08-07

## Current Status

- **Phase**: Phase 11 complete — ⭐ **product complete**, the second milestone gate. `src/api/synth/`
  (`findings.py`/`cluster.py`/`generate.py`/`bind.py`/`assemble.py`) turns graded claims into a
  `Report` matching the masterplan §2 contract exactly, with 100% sentence-to-claim binding verified
  both by assertion at assembly (`assemble.assert_binding` — actual name `_assert_binding`) and by a
  dedicated integration test that walks every claim id the report cites back to a real span in real
  source text. `make check`: **690 tests, 0 failures, 96.51% coverage overall**; every `src/api/synth/`
  module 98–100%. New in this phase: `src/api/llm/embed.py` (OpenRouter `/embeddings`, the first
  embedding provider this codebase has ever needed), migration `0009` (`embedding_cache`, pgvector),
  three new prompts (`synthesise_mvp.md`/`synthesise_gaps.md`/`synthesise_risks.md`), and `api.cli`
  wired to call `assemble_report` and persist the report at the end of every `run`. Proven both
  DB-backed (a large seeded-claims integration suite, real Postgres, real prompt files, scripted
  transports) and live (`python -m api.cli run` against real OpenRouter chat + the real new
  embeddings endpoint — see Recent Activities for the numbers and the two real bugs a first live
  attempt would have shipped silently: a prompt-caching/repair-round bug and an embedding
  cost-deduplication bug, both caught by tests before the live run, not by it).
- **Focus**: Begin Phase 12 — API, Auth, Quotas & Guardrails: an authenticated HTTP API with SSE in
  front of what `api.cli`/`api.synth` already do end to end.
- **Blockers**: None for Phase 12. Carried forward, all non-blocking: Reddit API application (still
  not submitted), the GitHub Starring endpoint's permanent restriction (resolved 2026-08-07 as a
  **vendor lockdown**, not a credential gap — a fine-grained PAT Starring permission cannot unblock
  it; see Next Steps item 2), `api.evidence.coverage.compute_coverage`'s entity-signal term reading
  `0.00` for essentially any real run (Phase 10, worth Phase 14's attention), and three new Phase 11
  items — see Next Steps: (a) the community thread-breadth approximation (`api.synth.findings`'s
  own module docstring), (b) `evaluate_github_theme` (reaction-weighted promotion) has no real
  caller in v1, (c) two frozen Phase 00 `Report` leaf
  fields were widened (`CompetitorEntry.maturity`, `ContradictionValue.v`) — logged, not hidden.

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

### 2026-08-07

- **Implemented Phase 04**: `src/api/search/` (Exa behind a `SearchProvider` protocol, credit-
  ledger allowance tracking, 24h result cache, per-run `RetrievalBudget`) and `src/api/sources/`
  (GitHub, HN Algolia, Wayback CDX, npm/PyPI, Stack Exchange, Product Hunt, SERP-snippets, Reddit).
  Migration `0004_search_domain_retrievers` adds `search_cache` and `search_credit_usage`
  (append-only ledger, dollars not query counts). `config.py` gains `producthunt_token`,
  `enable_reddit`/`reddit_client_id`/`reddit_client_secret`, and `exa_daily_credit_cap_usd`/
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
    `.packages`, `.stackexchange`, `.producthunt`, `.serp_snippets`, `.reddit`) exposes its own
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
  (the two distinct anecdote thresholds — 5 comments/3 threads for Reddit/HN, reaction-weighted
  for GitHub with no breadth requirement), `coverage.py` (cost-weight-weighted coverage, failed vs.
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
  - **Reddit remains the one open credential** — application not yet submitted (2–4 week manual
    approval); no change. `make check` green after the Product Hunt changes.

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

 1. **Submit the Reddit application** — still not done; 2–4 week manual approval once submitted,
    and it is not blocking anything else. Product Hunt is **closed** (developer token obtained and
    verified live 2026-08-07). Both now have real, tested `RetrieverUnavailableError` degradation
    paths in `api.sources.reddit`/`api.sources.producthunt`, so obtaining either credential is a
    config change, not a code change.
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
   venues are actually available — is deferred until Phase 10 shows whether Reddit credentials
   have landed by then.

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

## Open Items Carried From the Masterplan

| # | Item | Closes in |
|---|---|---|
| 1 | All quota and budget values | [Phase 14](execution_phases/phase-14-benchmark-calibration.md) |
| 2 | The ten benchmark queries + hand-verified ground truth | [Phase 14](execution_phases/phase-14-benchmark-calibration.md) |
| 3 | Whether Playwright is needed at all | **Closed in [Phase 01](execution_phases/phase-01-dependency-validation-spike.md): no — deferred behind a feature flag. Static crawl hit rate (88%) clears the masterplan's 80% bar; ships only if Phase 14 recall proves JS-rendering-limited.** |
