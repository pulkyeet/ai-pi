# Working Knowledge

## Project Overview

AI Product Investigator: type a product idea, get an evidence-backed discovery report in under
three minutes, where every sentence traces to a dated character span in a fetched page. Full
spec: [`ai-product-investigator-masterplan.md`](../ai-product-investigator-masterplan.md).
Execution order and phase-by-phase design: [`docs/execution_phases/`](execution_phases/README.md).

The whole design rests on one mechanism: a claim is only ever written if its quote is found
verbatim in stored page text (`source_text.find(quote)`), so every prose sentence in a report can
cite a real character span. Everything else — the closed claim vocabulary, computed confidence,
SQL-only contradiction detection — exists to make that one guarantee cheap to keep.

## Architecture

### High-Level Design

```
free text -> Interpret (ResearchBrief) -> Plan (task DAG)
  -> Executor (asyncio DAG over Postgres tasks, SKIP LOCKED leasing)
    -> fetch/search/domain-retriever tasks -> claim extraction (span-bound)
      -> entity resolution -> grading + confidence + contradictions
        -> synthesis (citation-constrained) -> Report (persisted, SSE)
```

See masterplan §3 for the full flow diagram and §4 for each stage's design.

### Key Components

- **Executor** ([Phase 02](execution_phases/phase-02-executor-core.md), **built** — see
  `src/api/executor/`): hand-rolled asyncio DAG over a Postgres `tasks` table (`SELECT ... FOR
  UPDATE SKIP LOCKED`). No Celery, no Redis, no arq — this table *is* the queue.
  `Executor.submit(run_id, plan, *, budget_weight, ...) -> AsyncIterator[ExecutorEvent]` is the
  only public entry point. Deliberately domain-agnostic: its `TaskSpec`/`ExecutionPlan`/
  `ExecutorEvent` types (in `api.executor.protocol`) use `kind: str`, not the Phase 00 `TaskKind`
  enum, and nothing under `src/api/executor/` imports `api.models.*` — real domain `Plan`s get
  adapted into this generic shape at the Phase 10 boundary. Hardened against a synthetic-only
  chaos suite (`tests/integration/test_chaos.py`): worker-kill + sweep recovery, zombie-write
  rejection (both idempotency guards independently), retry-storm containment, runaway-fan-out
  budget halt, all-branches-fail clean termination, timeout enforcement.
- **Fetch, text extraction & source cache** ([Phase 03](execution_phases/phase-03-fetch-source-cache.md),
  **built** — see `src/api/retrieval/`): `fetch_source(pool, client, throttle, robots, url, *,
  retrieval_reason) -> FetchOutcome` is the layer's single entry point — URL in, stored
  deduplicated `Source` out. Owns URL canonicalisation (`canonical.py`, proven idempotent), robots
  + hardcoded no-crawl compliance (`robots.py`), per-host politeness and conditional-request
  retries reusing Phase 02's retry policy unmodified (`fetch.py`), the **one and only**
  normalisation pass over extracted text (`extract_text.py` — span binding in Phase 06 depends on
  this being done exactly once, at write time, nowhere else), and the 7d/24h TTL source +
  path-guess caches (`cache.py`). Path-guessing (`pathguess.py`, `guess_path`) is the masterplan's
  primary cost lever — fetch `/pricing` directly instead of searching for it — measured at a real
  **75% hit rate** (`docs/external_apis.md`), below the masterplan's 80% assumption but quantified,
  not a blocker (see Recent Activities in `tracker.md`).
- **Search & domain retrievers** ([Phase 04](execution_phases/phase-04-search-domain-retrievers.md),
  **built** — see `src/api/search/` and `src/api/sources/`): `SearchRouter.search()`
  (`api.search.router`) is search's single entry point — cache lookup, then a credit-ledger
  allowance gate, then the one v1 `SearchProvider` (`api.search.exa.ExaProvider`), with both a
  provider failure and an exhausted allowance caught internally and turned into a degraded
  `SearchResponse` rather than raised ("degradation is a designed path, not an error path"). A
  separate, in-memory `RetrievalBudget` (`api.search.budget`) is a per-run *call-count* cap,
  unrelated to the credit ledger; it raises `BudgetExhaustedError` straight out of `search()`.
  Seven free structured retrievers live in `api.sources`: `github` (repo metadata, reaction-sorted
  issues, 90-day star velocity — the last one degrades to a coverage gap under the current PAT, see
  Known Issues), `hn`, `wayback`, `packages` (npm+PyPI), `stackexchange` (quota read from the
  response body, not headers), `producthunt` (slug-lookup only — `post_by_slug`, token obtained
  2026-08-07; Product Hunt v2 GraphQL has no text-search field, see its module docstring),
  `serp_snippets` (G2/Capterra — structurally cannot fetch; no `httpx` import in the module at
  all). All seven degrade via the shared
  `api.sources.base.RetrieverUnavailableError` rather than crashing a run. (Reddit was planned as
  an eighth but dropped — D5, its manual app-approval process was infeasible; search hits that
  point at reddit pages are still handled as ordinary page content.)
- **LLM gateway** ([Phase 05](execution_phases/phase-05-llm-gateway.md), **built** — see
  `src/api/llm/`): `gateway.structured(schema, prompt_id, variables, *, untrusted=None, ctx) ->
  LLMResult[T]` is the only way any module calls a model — enforced by a `TID251` ruff rule banning
  direct `api.llm.client` imports outside `api.llm` itself (`gateway.py`'s own `build_context()` is
  the one place a real caller needs to construct an `LLMContext`, so nothing outside `api.llm` ever
  needs to import `LLMClient` at all). `client.py` wraps OpenRouter with Phase 02's retry policy
  reused unmodified, `provider.require_parameters: true`, and `temperature: 0` always — Phase 01's
  own measured findings made real. `prompts.py` loads versioned `*.md` files (YAML frontmatter +
  `## section` bodies) into a cache-optimal static prefix (system message, byte-identical regardless
  of variables) plus a per-call user template; `prompt_version = f"{id}@{sha256[:8]}"` flows into
  `claims.extractor_version` as `f"{prompt_version}-{model}"` (this phase's resolution of Phase 00's
  open decision #2 — a model swap must invalidate cached extractions, so both go in). Untrusted page
  content is a distinct parameter, never templated: it is appended as its own `<untrusted
  name="...">...</untrusted>` block with `&`/`<`/`>` always entity-escaped first, so no payload —
  including one that literally contains the closing tag — can break out of its region. Exactly one
  repair retry on a schema/parse failure; a second failure raises `LLMValidationError` carrying only
  a scrubbed validation summary (`ValidationError.errors(include_input=False)`), never the raw
  payload. `cost.py` holds the one per-model rate table (Phase 01's measured
  deepseek/deepseek-v4-flash numbers) and the `llm_calls` ledger (cost, cache-hit, and repair rate
  all real SQL aggregates, attributed to `run_id`/`task_id`). `cache.py` is a permanent,
  content-addressed Postgres response cache (`hash(prompt_version + model + rendered_messages)`) —
  a transport-level cache, deliberately distinct from Phase 06's future `content_hash +
  extractor_version` extraction cache. `tracing.py` wraps Langfuse behind a `Tracer` protocol whose
  `NoopTracer`/`LangfuseTracer.record()` both guarantee never raising, so a Langfuse outage can never
  fail a run.
- **Claim extraction & span binding** ([Phase 06](execution_phases/phase-06-claim-extraction-span-binding.md),
  **built** — see `src/api/extract/`): `extractor.extract_claims(source, *, ctx) -> ExtractionResult`
  is the entry point, one `Source` per call, never batched. The core guarantee lives in
  `span.bind_span(source_text, quote) -> Span | None` — masterplan §4.8, verbatim: `str.find`, no
  fuzzy matching, an ambiguous (>1 occurrence) quote drops rather than resolving to the first match.
  Consumes `Source.extracted_text` from Phase 03 as its byte-identical input — the reason that
  layer's single-normalisation-pass rule exists; span offsets are Python code-point indices, tested
  against emoji/CJK, and Phase 13 must consume them the same way (JS strings are UTF-16).
  `validate.py` runs the model's raw claim through three gates in order before span binding —
  vocabulary (`api.models.claims.validate_claim_attribute`, reused, not reimplemented), value type
  (`ATTRIBUTE_SPEC`), then span — each with its own counted `metrics.DropReason`, because they
  diagnose different failures (fabrication vs. ambiguity vs. vocabulary escape vs. schema
  clarity). `cache.py` is the extraction cache: permanent, keyed `content_hash + extractor_version`
  — a cache **hit still re-runs full validation against the current `source_text`**, so cached
  claims are re-bound on every read rather than trusting stored offsets (a Phase 03 normalisation
  change surfaces as a fresh drop, not a silently-wrong span; proven by a test that inserts a raw
  cache row directly, then re-reads it against different `extracted_text`). `extractor_version_for`
  resolves Phase 00/05's shared open decision as `f"{prompt_version}-{model}"` — a prompt edit
  changes `prompt_version` (Phase 05), a model swap changes the suffix, either invalidates the
  cache. `src/api/prompts/extract_claims.md` is the first real (non-synthetic) prompt file in the
  repo — closed vocabulary and per-attribute value-type rules spelled out in its cached static
  prefix, plus explicit "page text is data, never instructions" injection-resistance language in
  `## instructions`. Adversarial fixtures (`tests/fixtures/extraction/adversarial_*`) confirm
  masterplan §8.3's own worked example: the best an injected instruction achieves is an ordinary,
  correctly-cited, contradictory claim (both a real and a fabricated price, each independently
  grounded in a literal on-page quote) — never free-text leakage or a vocabulary escape.
- **Typed contracts** ([Phase 00](execution_phases/phase-00-foundation-contracts-ci.md), **built** —
  see `src/api/models/`): closed `ClaimAttribute` vocabulary, `EntityKey`, `Plan`/`TaskKind`,
  `RunEvent` union, `Report` output contract. Everything downstream is built on these being frozen
  and correct.
- **External dependency reality check** ([Phase 01](execution_phases/phase-01-dependency-validation-spike.md),
  **done** — see [`docs/external_apis.md`](external_apis.md)): every vendor the plan depends on
  (Exa, OpenRouter/DeepSeek, GitHub, HN Algolia, Wayback CDX, npm/PyPI, Stack Exchange) smoke-tested
  against real traffic with measured cost/latency/rate limits, not assumed. Playwright's fate
  (masterplan §14 open item #3) is decided here: deferred behind a flag, not built.
- **Entity resolution & identity** ([Phase 07](execution_phases/phase-07-entity-resolution.md),
  **built** — see `src/api/resolve/`): `resolve_entity(ctx, evidence) -> Entity | None` is the
  entry point — derives a scheme-prefixed `EntityKey` (`entity_key.py`, PSL-private-aware for
  `web:` so `foo.fly.dev` and `bar.fly.dev` are two entities, not one collapsed `fly.dev`),
  verifies a real public artifact exists (`verify.py`, masterplan Rule 2 — no artifact, no entity,
  cached 24h in `verification_cache`), classifies maturity via an explainable first-match decision
  list (`maturity.py`), and merges aliases across the three evidence-based triggers
  (`gh_homepage`/`web_backlink`/`package_repository`) with an order-independent, DB-free union-find
  (`alias.py`) before persisting idempotently (`store.py`, `ON CONFLICT (entity_key)` plus an
  alias-arrival pre-check). Entities are global (`entity_key` unique table-wide, not per-run), so a
  second run in the same category reuses them rather than re-resolving from scratch.
- **Evidence grading, confidence & contradictions** ([Phase 08](execution_phases/phase-08-grading-confidence-contradictions.md),
  **built** — see `src/api/evidence/`): no orchestrating entry point like `resolve_entity` — each
  module is independently callable, matching the phase doc's own framing that this phase produces
  "deterministic confidence on every claim, and a contradiction detector", not a pipeline.
  `grade.py`'s `grade_for`/`classify_own_domain_fetch` cover the two provenance decisions a
  retriever's own flat `grade` attribute (`api.sources.base.Retriever`) can't make alone — own-
  domain path (pricing/docs vs. blog/changelog) and Wayback's inherit-and-cap-at-B rule.
  `confidence.py` implements masterplan §4.6's formula verbatim (`BASE`/`DOMAIN_BONUS_PER_STEP`/
  `DECAY_PER_30_DAYS`/`CONTRADICTION_PENALTY`/`CONFIDENCE_CAP`, all named and independently tunable
  in Phase 14) plus `distinct_domain_count` (reuses Phase 07's PSL-aware `derive_web_key`, so
  `docs.foo.com`/`www.foo.com` collapse to one domain — corroboration means independent sources,
  never distinct pages on the same site) and `age_days` (a claim's own `as_of` wins over
  `fetched_at`). `contradictions.py` is masterplan §4.7's `GROUP BY` (grade D excluded) extended
  with a per-`ATTRIBUTE_SPEC` comparison rule (numeric on `value_num` — Postgres `numeric` is exact
  decimal, so `$5.00`/`$5` already compare equal; everything else on normalised `value_text`);
  **`_is_contradictory` returns `False` for any `ValueKind.LIST` attribute** — `product.integrations`/
  `product.platforms` are legitimately multi-valued (Phase 14 follow-up 2026-08-10; the 8/8 benchmark
  false-positive class was exactly these), so the detector fires only on genuinely single-valued
  attributes;
  resolution is highest-grade-wins/tie-on-recency, losers are **retained** via `superseded_by`
  (never deleted), and the winner's confidence is recomputed with the 0.6 penalty from its own
  stored `claims.confidence_inputs` (migration `0008`, the one schema change this phase needed —
  formula inputs must survive alongside the result for auditability/recomputation, per the phase
  doc's own exit criterion). `promotion.py` holds the two distinct anecdote-eligibility rules
  (comment volume + breadth for community themes; reaction-weighted, no breadth, for GitHub) — statement
  generation itself is Phase 11's job. `coverage.py` computes cost-weight-weighted coverage with
  failed/budget-skipped/other-skipped branches kept distinct, folding in Phase 07's
  `insufficient_signal` entities as a second signal. **Zero LLM calls anywhere in this package**,
  enforced by an AST import-check test, not just convention — matching `api.sources.serp_snippets`'s
  own "no `httpx` import at all" structural guarantee for the analogous G2/Capterra no-crawl rule.
- **API, Auth, Quotas & Guardrails** ([Phase 12](execution_phases/phase-12-api-auth-quotas.md),
  **built** — see `src/api/web/`): `app.create_app(settings, pool, http) -> FastAPI` — pool/http are
  always caller-built (no FastAPI `lifespan=`), so `api.web.main` (the `uvicorn.serve()` production
  entrypoint) and every test own their own resources' teardown explicitly, the same pattern `api.cli`
  already uses. `auth.py`'s `JWKSCache` verifies Supabase JWTs locally against a cached JWKS
  (refetched only on a `kid` miss), with five independently-coded rejection reasons
  (`token_expired`/`wrong_issuer`/`wrong_audience`/`bad_signature`/`malformed_token`); `provision_user`
  is idempotent in one round trip. `quota.py`'s `try_create_run` is the masterplan §8.3 atomic quota
  check, made *actually* atomic with a `pg_advisory_xact_lock` (see Known Issues — the naive
  conditional `INSERT` alone admitted 8/8 under real concurrency, not 7/8); `ConcurrencyQueue` is an
  in-memory, per-process FIFO admission gate with a queryable position, the same accepted
  single-worker limitation as Phase 02's `BudgetTracker`. `killswitch.py` is a `system_state`
  singleton row, tripped automatically by the route layer on a global-quota miss, reset only by an
  operator (no HTTP endpoint — none is in the phase doc's own Endpoints table). `sse.py` owns the
  masterplan §4.10 six-event public vocabulary (`plan.created`/`task.started`/`task.completed`/
  `task.failed`/`finding.added`/`report.ready`) — deliberately smaller than and different from
  `api.executor.protocol.ExecutorEvent`, sharing Phase 02's `run_events` table; `stream_events` closes
  on `report.ready` or a `failed` run status. `runner.run_pipeline` is `api.cli.cmd_run`'s pipeline
  adapted to run as a background task, reusing `api.cli.build_deps`/`plan_to_execution_plan`/
  `run_coverage` rather than re-deriving them, with one real behavioural difference: it genuinely
  pauses on disambiguation (`runs.status = 'needs_input'`) until `PATCH /runs/{id}` resumes it — an
  ordinary HTTP round trip, not an in-graph interrupt. `errors.py` gives every response a stable
  `code` and a correlation id, never a stack trace or vendor message.

## Important Patterns & Conventions

### Code Style

- `ruff` for lint + format (replaces black/isort/flake8), `mypy --strict` on `src/api/`.
- Scope lint/format/type commands to `src tests migrations` — **never run `ruff format .` at the
  repo root.** It will reformat embedded Python code fences inside the markdown docs (masterplan,
  phase docs), which are hand-aligned documentation, not source. This bit us once during Phase 00;
  the `Makefile` targets are scoped specifically to prevent a repeat.
- Raw SQL via asyncpg, not an ORM — `db.py` is a thin pool/connection helper. Chosen because the
  executor's `FOR UPDATE SKIP LOCKED` and the contradiction `GROUP BY` are both clearer as SQL, and
  the whole point of the schema is that it's simple enough to hand-write and reason about directly.
  (Alembic itself still needs a synchronous SQLAlchemy engine to *run* migrations — `psycopg` +
  `sqlalchemy` are migration-only dependencies; the app runtime path stays asyncpg-only.)
- `StrEnum` + Pydantic everywhere a vocabulary needs to be closed. If you're tempted to accept a
  bare `str` for something like a claim attribute or entity scheme, don't — the whole system's
  guarantees (contradiction detection, injection resistance) depend on these being enumerable.

### Phase 14 follow-up conventions (2026-08-10)

- **`pricing.model` vocabulary is `seat|usage|flat|freemium|free`.** `free` is the honest value for
  a permanently-free product (no paid tier); `freemium` *implies* a paid tier above the free one,
  so using it for an OSS tool would fabricate a claim. `build_competitors` accepts
  `entry_usd_month=0.0` when `model=='free'` (the only truthful number) — a non-free entity still
  requires the full triple.
- **Contradictions never fire on `ValueKind.LIST` attributes** (`product.integrations`/
  `product.platforms` are multi-valued by nature); the `GROUP BY` detector applies only to
  single-valued attributes.
- **Discovery never seeds from `awesome-*` curated-list repos** (`discover._is_github_list_repo`).
  When `consider_oss` is true it searches GitHub for the category itself
  (`"<cat> in:name,description stars:>100"`); general Exa search is the primary channel.
  `DISCOVERY_SEARCH_LIMIT=20` (up from 10) and Exa `mode="auto"` give household names ranked
  outside the top-10 a chance to surface.
- **Exa snippets are quoteable evidence (decision 06b)**: a search-result snippet becomes a grade-C
  synthetic source (`retrieval_reason='serp_snippet'`, canonical URL `https://<root>#serp-snippet`,
  never collides with a real homepage fetch) that `profile_product` extracts claims from. **`pricing.*`
  claims are dropped from snippet extraction** — a machine summary can never complete the competitor
  pricing triple. Snippet provenance shows as grade C, not the vendor's own page.
- **`merge_alias` repoints claims before deleting the losing entity** — `claims.entity_id` has no
  cascade (deliberate), so a merge that deleted first raised `claims_entity_id_fkey` (q08 live, and
  cached-only replay). The canonical entity absorbs the losing entity's claims.
- **No quote-length floor on extraction (decision 06a)** — the measured drop causes are HTML-entity
  mismatch (`&amp;` in quotes vs decoded stored text), non-vocabulary attributes, and
  text-where-typed values, not short quotes.
- **Storage per run ≈ 0.40 MB (measured 2026-08-10, replaces the unmeasured ~1.2 MB masterplan
  estimate).** Averaged over the 8 largest real runs in `ai_pi`: min 0.37 MB, max 0.50 MB, mean 0.40 MB.
  `sources.extracted_text` (fetched page bodies) and `claims` dominate; `llm_calls` stores only
  token/cost metadata (no prompt/response payloads), so LLM traffic adds negligible per-run bytes.
  `entities`/`entity_aliases` are a global identity store shared across runs and are excluded from the
  per-run figure. Implication for eviction: at 0.40 MB/run, the 500 MB Supabase ceiling holds ~1,250
  runs before eviction — the hybrid "Postgres on Fly" escape hatch (tracker.md Supabase-over-Neon
  section) is **not** triggered on storage grounds.

### Naming Conventions

- One module per concern under `src/api/`; no `utils.py`.
- Python package root is `src/api/`, installed editable via `pyproject.toml`.
- Every LLM prompt (from Phase 05 onward) lives in a versioned file under `src/api/prompts/`,
  never inline in code, so `extractor_version` is meaningful.

### File Organization

```
src/api/
├── config.py, db.py, logging.py     # Settings, asyncpg pool, structlog+OTel bootstrap
├── models/                          # Pydantic contracts (Phase 00 — built)
├── executor/                        # Domain-agnostic task DAG executor (Phase 02 — built):
│                                     # core.py (Executor.submit), lease.py (claim/renew/
│                                     # complete/fail/sweep), budget.py, retry.py, protocol.py
│                                     # (own TaskSpec/ExecutionPlan/ExecutorEvent — no
│                                     # api.models.* imports; see Key Components)
├── retrieval/                       # Fetch, extraction & source cache (Phase 03 — built):
│                                     # fetch.py (fetch_source, HostThrottle, retry+conditional
│                                     # requests), canonical.py, extract_text.py (trafilatura +
│                                     # the one normalise() pass), pathguess.py (guess_path,
│                                     # PRICE_TOKEN_RE), robots.py (RobotsCache, no-crawl set),
│                                     # cache.py (source + path-guess cache), errors.py
├── search/                          # Search provider + allowance tracking (Phase 04 — built):
│                                     # router.py (SearchRouter.search — cache, credit-ledger
│                                     # allowance, degradation), exa.py (ExaProvider, the only v1
│                                     # SearchProvider), budget.py (RetrievalBudget — a *different*
│                                     # in-memory call-count cap, not the credit ledger), cache.py
│                                     # (24h search-result cache), base.py (SearchProvider
│                                     # protocol, SearchResult/SearchResponse)
├── sources/                         # Domain retrievers (Phase 04 — built): github.py, hn.py,
│                                     # wayback.py, packages.py (npm+PyPI), stackexchange.py,
│                                     # producthunt.py, serp_snippets.py (G2/Capterra, no httpx
│                                     # import at all); base.py
│                                     # (Retriever marker protocol, RetrieverUnavailableError),
│                                     # ratelimit.py (TokenBucket, one per retriever/endpoint)
├── llm/                              # LLM gateway (Phase 05 — built): gateway.py
│                                     # (structured() — the only entry point, LLMContext,
│                                     # build_context()), client.py (OpenRouter transport — import
│                                     # banned outside api.llm by TID251), prompts.py (PromptRegistry,
│                                     # render_messages), cost.py (MODEL_RATES, llm_calls ledger),
│                                     # cache.py (transport-level response cache), tracing.py
│                                     # (Langfuse, Tracer protocol)
├── extract/                         # Claim extraction & span binding (Phase 06 — built):
│                                     # extractor.py (extract_claims, extractor_version_for),
│                                     # span.py (bind_span — no fuzzy matching anywhere in this
│                                     # module, quote_context_window), validate.py
│                                     # (RawExtractedClaim/ExtractedClaim/ExtractionResponse, the
│                                     # vocabulary→value-type→span gate pipeline), cache.py
│                                     # (content_hash+extractor_version, permanent, re-binds on
│                                     # read), metrics.py (DropReason, DropCounts, ExtractionMetrics)
├── resolve/                         # Entity resolution & identity (Phase 07 — built):
│                                     # entity_key.py (derive_key, PSL-aware web: derivation),
│                                     # verify.py (verify_entity, masterplan Rule 2), alias.py
│                                     # (order-independent union-find over the 3 merge triggers),
│                                     # maturity.py (derive_maturity, first-match decision list),
│                                     # store.py (upsert_entity, merge_alias), types.py
│                                     # (EntityEvidence, VerificationContext); __init__.py wires
│                                     # all five into resolve_entity(ctx, evidence)
├── evidence/                        # Grading, confidence & contradictions (Phase 08 — built):
│                                     # grade.py (SourceKind, grade_for, classify_own_domain_fetch),
│                                     # confidence.py (confidence, ConfidenceInputs,
│                                     # distinct_domain_count, age_days — masterplan §4.6 verbatim),
│                                     # contradictions.py (find_contradiction_groups,
│                                     # resolve_contradictions — masterplan §4.7's GROUP BY +
│                                     # per-attribute comparison + resolution), promotion.py
│                                     # (evaluate_community_theme, evaluate_github_theme),
│                                     # coverage.py (compute_coverage, cost-weight-weighted). No
│                                     # single orchestrating entry point — each module is called
│                                     # independently by Phase 10/11
├── prompts/                         # versioned prompt files: extract_claims.md (Phase 06 — the
│                                     # first real, non-synthetic prompt in the repo), Phase 09/11's
│                                     # own interpret/plan/synthesise prompts
└── web/                             # API, Auth, Quotas & Guardrails (Phase 12 — built):
                                      # app.py (create_app), auth.py (JWKSCache, verify_token,
                                      # current_user/optional_user), quota.py (try_create_run,
                                      # ConcurrencyQueue), killswitch.py, turnstile.py, sse.py
                                      # (masterplan §4.10's 6-event public vocabulary), runner.py
                                      # (run_pipeline — the background POST /runs pipeline),
                                      # errors.py (typed APIError hierarchy), main.py (uvicorn
                                      # production entrypoint), routes/{runs,reports,health}.py
migrations/versions/                 # hand-written, reviewed Alembic migrations
spikes/                              # Phase 01 throwaway vendor smoke tests, plus Phase 03's
                                      # pathguess_hitrate.py measurement script — kept as the
                                      # reproducible evidence behind docs/external_apis.md;
                                      # `import spikes` from src/api is banned by a ruff rule
tests/
├── unit/          # pure functions, no I/O, always run
├── integration/    # real Postgres, replayed HTTP fixtures; also the Phase 01 fixture-corpus
│                    # integrity tests (secret-scrub + offline-replay), which need no Postgres;
│                    # _synthetic.py/_db.py/_worker_process.py (leading underscore, not
│                    # collected as tests) back the Phase 02 executor/chaos suites; _http.py
│                    # backs Phase 03's fetch/cache/pathguess tests (and Phase 04's
│                    # MockTransport-only retrievers) with a scripted httpx.MockTransport
│                    # instead of VCR cassettes; _vcr.py (Phase 04) is a from-scratch,
│                    # secret-scrubbing VCR factory for replaying Phase 01's real cassettes —
│                    # a separate module from spikes/_common.py's, not an import of it (see
│                    # tracker.md); _auth.py (Phase 12) is a throwaway EC keypair + Supabase-shaped
│                    # JWT signer/JWKS response, and _webapp.py (Phase 12) builds a real
│                    # api.web.app.create_app() against a real pg_pool plus a scripted
│                    # httpx.AsyncClient standing in for Supabase JWKS + Cloudflare Turnstile —
│                    # backing test_api.py/test_quota.py/test_sse.py/test_runner.py
├── live/           # real external APIs, @pytest.mark.live, excluded by default; includes
│                    # test_pathguess_hitrate.py (Phase 03's 75% path-guess hit-rate guard),
│                    # test_domain_retrievers_live.py (Phase 04 — exercises api.search/
│                    # api.sources directly, not just raw HTTP like test_vendors.py does), and
│                    # test_llm_gateway_live.py (Phase 05 — the phase doc's two nightly checks:
│                    # schema-violation rate and OpenRouter prompt-cache hit — see tracker.md)
├── _llm_fixtures.py  # Phase 05: a synthetic prompt schema (EchoResult, PlanExtraction) shared by
│                      # every llm unit/integration/live test — importable from any tests/
│                      # subdirectory because tests/conftest.py's own directory (tests/) is always
│                      # on sys.path, unlike tests/integration/_http.py-style sibling helpers
└── fixtures/{cassettes,pages,prompts,extraction}/  # VCR cassettes (7 vendors + llm_openrouter.yaml,
                                      # reused by Phase 04/05's tests) + 40 real pricing pages,
                                      # ~30MB total — the Phase 01 fixture corpus, reused as-is by
                                      # Phase 03's extraction-quality tests and Phase 05's live
                                      # schema-violation check; prompts/ holds Phase 05's own
                                      # synthetic *.md fixtures (echo.md, extract_plan.md,
                                      # cache_probe.md) — never real domain prompt content, which is
                                      # Phase 09/11's scope now. extraction/ (Phase 06) is a separate,
                                      # smaller corpus of hand-authored already-extracted-text pages
                                      # (`.txt`, not `.html` — text-extraction quality is Phase 03's
                                      # corpus's job, not this one's) paired with a committed fake
                                      # LLM response (`.llm.json`) and expected surviving
                                      # claims/drop-reason-counts (`.expected.json`), so the corpus
                                      # runs fully offline with no real model call
docs/
├── tracker.md            # this file's sibling — living status log
├── working_knowledge.md  # this file — architecture/conventions reference
├── external_apis.md      # Phase 01 deliverable — measured vendor limits/costs/verdicts
└── execution_phases/     # the 16-phase build plan, one doc per phase
```

## Key Technologies & Dependencies

- **FastAPI + Python 3.12 + asyncio** — API layer (Phase 12 — built, see `src/api/web/`).
- **PyJWT + `cryptography`** — Supabase JWT verification (`api.web.auth`), JWKS-based (RS256/ES256),
  local, no per-request network call. **`sse-starlette`** — the `GET /runs/{id}/events` stream.
  **`uvicorn`** — the ASGI server, `api.web.main`'s production entrypoint only.
- **Postgres 16 + pgvector** — sole datastore; pgvector reserved for complaint near-dup detection
  (Phase 11), unused today but the extension is enabled from migration `0001`.
- **asyncpg** — app runtime DB access. **psycopg + SQLAlchemy** — Alembic migration runner only.
- **Alembic** — hand-written migrations, autogenerate off.
- **pydantic / pydantic-settings** — every contract and all config.
- **structlog + OpenTelemetry** — structured logging and tracing, wired from Phase 00 so no phase
  ships without observability.
- **uv** — dependency management and the Python interpreter itself (pins 3.12; this environment's
  system Python is 3.11, uv fetches 3.12+ on demand).
- **Supabase** (Postgres + Auth) is the target for deployed environments; local dev uses
  `docker-compose.yml`'s `pgvector/pgvector:pg16` with a migration-created `auth.users` stub so the
  schema is identical without a real Supabase project.
- **Exa** (search) and **OpenRouter** (`deepseek/deepseek-v4-flash` for extraction) — both
  validated in Phase 01; see [`docs/external_apis.md`](external_apis.md) for measured cost/recall.
- **httpx + trafilatura** — core `src/api` dependencies since Phase 03 (`api.retrieval` fetches
  and extracts with them for real). `vcrpy` stays a `dev`-only dependency: the Phase 01
  fixture-corpus integrity tests need it permanently, but nothing under `src/api` replays
  cassettes. `playwright` stays `spikes`-only — still deferred behind a feature flag, unbuilt.
- Full stack table and the reasoning behind each choice: masterplan §11 and §12 (decision log).

## Common Workflows

### Setting Up Development Environment

```bash
uv sync --extra dev          # installs deps incl. dev tools, uv fetches Python 3.12 if needed
cp .env.example .env         # fill in DATABASE_URL, OPENROUTER_API_KEY, EXA_API_KEY, GITHUB_TOKEN
make db-up                   # docker-compose up postgres, waits for pg_isready
# create the test DB once (not scripted yet — see Known Issues)
docker compose exec -T postgres psql -U postgres -c "CREATE DATABASE ai_pi_test;"
make migrate                 # alembic upgrade head
```

### Running Tests

```bash
make check      # ruff check + format --check + mypy --strict + pytest (== CI)
make unit        # fast tier only, no Postgres needed
make integration  # needs make db-up first (and the ai_pi_test DB — see above)
```

Integration tests skip gracefully (not fail) if no Postgres is reachable at
`TEST_DATABASE_URL` (defaults to `postgresql://postgres:postgres@localhost:5432/ai_pi_test`).

CI (`.github/workflows/ci.yml`) runs `make migrate` against `TEST_DATABASE_URL` before `make
check` — the Phase 02 executor integration tests assume the schema already exists rather than
self-migrating (only `test_migrations.py` does that). A separate nightly workflow
(`.github/workflows/nightly.yml`) runs `tests/integration/{test_lease,test_executor,test_chaos}.py`
50x via `pytest-repeat` (`--count=50`), per the Phase 02 exit criterion that concurrency fixes be
proven flake-free under repetition, not just a single green run. Run the same locally with
`uv run pytest tests/integration/test_lease.py tests/integration/test_executor.py tests/integration/test_chaos.py --count=N --no-cov`.

### Building for Production

Phase 15 (Deployment, Observability & Cost Control) built the code/deploy layer; the **actual
deploy is a separate go/no-go** (per `docs/tracker.md`). Topology: Fly.io (two machines — API
`python -m api.web.main` + worker `python -m api.worker`, **one image**, different entrypoints) +
Supabase (Postgres + Auth) + Vercel (frontend). Deploy order is **migrations → worker → API**
(`.github/workflows/deploy.yml`); nightly keepalive/maintenance/backup cron lives in
`.github/workflows/keepalive.yml`; operations in [`docs/runbook.md`](runbook.md). Cost target:
two `shared-cpu-1x` machines ≈ $6–8/mo fixed, ~$0.062/run measured (not the masterplan's "$5/mo
near zero" — Fly's free tier is gone; the README and runbook say so).

**Live URLs (verified 2026-08-19):** homepage/static frontend
`https://ai-pi-kohl.vercel.app/` (Vercel project `pulkyeet/ai-pi`; the default
`ai-pi.vercel.app` is squatted — a third-party redirect — so never use it), API +
`GET /reports/benchmark` at `https://ai-product-investigator.fly.dev`, Supabase project ref
`kftmstgqsepxuvuedseq` (region ap-south-1).

### Phase 15 additions to the architecture (2026-08-11)

- **`src/api/maintenance.py`** (`python -m api.maintenance`) — the nightly storage jobs for the
  500 MB Supabase ceiling: `api.retrieval.cache.evict_expired` (unpin-evict expired
  `sources.extracted_text`; rows and `claims.quote_context` survive so drill-down still works),
  `prune_expired_events` (30-day `run_events` window — the table was append-only and never pruned
  before), `pin_benchmark_sources` (sources cited by `is_benchmark` runs are pinned), and a
  `pg_database_size` query. Runs from the keepalive workflow; the worker machine also runs it
  in-Fly as redundancy.
- **`src/api/worker.py`** (`python -m api.worker`) — worker machine entrypoint: periodic
  `lease.sweep_expired` crash-recovery sweep + maintenance + `worker.health` log line. **Not a task
  runner** — runs execute inside the API process (single-worker design); handing execution over is
  future work.
- **`GET /metrics`** (authenticated, `src/api/web/routes/metrics.py`) — the runbook's nine-alert
  values; sentence-binding rate is the only `<100%`-pages alert. Backed partly by migration
  `0012_run_stats` (durable extraction drop counts, written at run-finish by `cli.run_query` and
  `web.runner.run_pipeline`).
- **`RUN_TIMEOUT_S` is wired** — `executor.submit(run_timeout_s=…)` + `lease.skip_rest`; past the
  deadline no new work is claimed, still-pending/running tasks are skipped (`reason='run_timeout'`),
  in-flight work finishes, report path still runs.
- **Worker machine lifecycle:** created/updated via `fly machine run/update --config fly.worker.toml`
  (machines created this way are ignored by `fly deploy`, so an API deploy can't clobber it); the
  API machine is managed by `fly deploy` itself.


## Known Issues & Gotchas

- **`array_length(arr, 1) >= 1` does not reject empty Postgres arrays.** `array_length` returns
  `NULL` for `ARRAY[]`, and `NULL >= 1` is `NULL`, which a `CHECK` constraint treats as *passing*
  (a constraint only fails on an explicit `FALSE`). The `findings_must_cite` constraint originally
  used this pattern and silently allowed empty `claim_ids`, defeating rule 1 of the masterplan
  ("every finding cites at least one claim"). Fixed by switching to `cardinality(arr) >= 1`, which
  returns `0` (not `NULL`) for an empty array. Caught only once live-Postgres integration tests
  actually ran — a reminder that `make check`'s DB-backed tier is not optional to exercise before
  trusting a schema change. Any future array-emptiness constraint should use `cardinality`, not
  `array_length`.
- **Never use `id(obj)` to generate "unique" test fixture values (emails, URLs, etc.) across test
  functions in the same process.** CPython reuses object ids after garbage collection, so two
  different tests can produce the same "unique" value and collide on a real unique constraint. Use
  `uuid.uuid4()`. Bit `tests/integration/test_migrations.py` once; fixed there.
- **`ruff format .` at the repo root reformats Python fences embedded in markdown docs** — see Code
  Style above. Always scope to `src tests migrations`.
- **Docker isn't always reachable from a WSL shell** even when Docker Desktop is running on
  Windows — needs WSL integration enabled in Docker Desktop settings first. If `docker compose`
  commands fail with "command not found" or connection errors, check that before assuming
  Postgres itself is misconfigured.
- **`ai_pi_test` database is not auto-created.** `docker-compose.yml` only provisions `ai_pi`
  (the dev DB). Integration tests default to `ai_pi_test`. Create it manually once per fresh
  container (see Setup above), or point `TEST_DATABASE_URL` at `ai_pi` instead. Worth scripting
  into `make db-up` in a later phase if this keeps tripping people up.
- **Stack Exchange's quota is reported in the JSON response body** (`quota_max`/`quota_remaining`
  fields), never in HTTP headers, despite what you'd assume from every other rate-limited API.
  Poll the body, not `response.headers`.
- **GitHub's Starring endpoint (`/repos/{owner}/{repo}/stargazers`) is a permanent NO-GO, not a
  credential fix.** Since 2026-06-30 GitHub restricts it to repo **admins/collaborators only**, and
  fine-grained PATs are not supported for it at all (no fine-grained permission exists; a classic
  PAT with `public_repo` works only when the token's owner is an admin/collaborator of the target
  repo). We are never that for competitor repos, so `star_velocity_90d` cannot be unblocked by any
  PAT change — a fine-grained Starring permission has no effect. Only total `stargazers_count`
  (via `repo_metadata`) remains readable. Needed for the masterplan's 90-day star-velocity signal
  ([Phase 04](execution_phases/phase-04-search-domain-retrievers.md)/[07](execution_phases/phase-07-entity-resolution.md)).
- **GitHub's Search API has its own, much stricter rate limit: 30 req/min**, separate from the
  general REST 5,000 req/hr. The masterplan's `is:issue label:X sort:reactions-desc` query pattern
  must budget against 30/min, not 5,000/hr.
- **Vendors localize pricing pages by request geography.** `hubspot.com/pricing` served ₹ (INR)
  pricing to this environment's egress IP, not $. A price-detection regex that only matches `$`
  will silently misclassify a real, successfully-fetched page as a crawl failure. Match
  `[$€£¥₹]` plus currency codes, not just `$`.
- **OpenRouter's default routing is not sticky to one backend node**, so a provider's server-side
  prompt cache (tested against DeepSeek) mostly misses even across byte-identical prefixes fired
  back-to-back — measured 1/10 hit rate in Phase 01. The saving is real when it lands (matches the
  pricing ratio exactly), but don't count on it in a cost model without further work (e.g. pinning
  to a single upstream provider, not just parameters).
- **LLM extraction calls without a strict `response_format` schema have unbounded, unpredictable
  output length and tail latency.** Measured p95 62.1s (up to 6,266 completion tokens for one call)
  free-form, versus p95 14.7s with schema enforcement on the same prompt shape. Always use
  `response_format` for extraction calls — see [Phase 05](execution_phases/phase-05-llm-gateway.md)/[06](execution_phases/phase-06-claim-extraction-span-binding.md).
- **Never gate a terminal-state decision on "nothing happened in a time window" when a precise,
  state-based predicate is available instead.** The executor's first dead-branch detector
  ("nothing claimable + nothing running + something pending ⇒ unreachable, skip it") raced against
  retry backoff: a task waiting out a very short jittered delay (full jitter can land near zero)
  could have that delay elapse in the gap between the dispatch loop's claim attempt and its
  dead-branch check, and get skipped instead of retried. It passed on every single unrepeated run
  and only showed up under `pytest-repeat` (~1 failure in 6 on the tightest-timing test) — exactly
  the "concurrency bugs are probabilistic, a single green run is weak evidence" trap [Phase
  02](execution_phases/phase-02-executor-core.md) warns about. Fixed by making
  `api.executor.lease.skip_unreachable` depend only on dependency state (a `pending` task is dead
  iff one of its `depends_on` names a `failed`/`skipped`/missing task — never on timing), which is
  both simpler and provably race-free. Re-verify any future "is this actually stuck or just slow"
  logic against `pytest-repeat` before trusting a single green run.
- **`tasks.lease_expires_at` is intentionally dual-purpose.** While `status='running'` it's the
  lease deadline (crash-recovery sweep target); while `status='pending'` after a retryable
  failure, the same column holds "earliest retry time" (`lease.claim_next`'s claimability filter
  checks both meanings the same way: `lease_expires_at IS NULL OR lease_expires_at <= now()`).
  Deliberate, to avoid a second column for what is, in both cases, "don't touch this row before
  this timestamp" — see [Phase 02](execution_phases/phase-02-executor-core.md).

- **Percent-decoding a URL component for canonicalisation must not decode *reserved* characters.**
  `%2F` inside a path segment is an encoded literal slash — decoding it to a real `/` silently
  turns one path segment (`foo%2Fbar`) into two (`foo/bar`), changing what the URL means, not just
  how it's spelled. `api.retrieval.canonical._renormalise_percent_encoding` only decodes `%XX`
  escapes that spell an RFC 3986 *unreserved* character (letters, digits, `-._~`); every other
  escape (including `%2F`) keeps its `%XX` form, just uppercased. Caught by a hand-written
  canonicalisation-table test before it reached the Hypothesis property test — see [Phase
  03](execution_phases/phase-03-fetch-source-cache.md).
- **A "does this page mention a price" check must specify whether it matches raw HTML or extracted
  text — the two give different answers.** Phase 01's crawl-viability spike counted a hit if the
  price regex matched *either* the trafilatura-extracted text *or* the raw HTML body, which catches
  prices sitting in `<script>` JSON blobs that trafilatura correctly excludes as boilerplate.
  `api.retrieval.pathguess.guess_path` matches only the extracted, normalised text — it has to,
  since that's the same text stored in `sources.extracted_text` and the text Phase 06 will bind
  spans against. Real consequence, not just a theoretical distinction: Phase 03's real path-guess
  hit rate came out to 75%, below Phase 01's 82%, for exactly this reason on 4 of the 10 newly
  visible misses (`vercel.com`, `zoom.us`, `stripe.com`, `sendgrid.com` — see
  `docs/external_apis.md`'s "Fetch & path-guessing (Phase 03)" section for the per-domain
  root-cause). When comparing "hit rate" numbers across phases or vendors, always check which text
  the match ran against.
- **A test against a deliberately shared/persistent cache or ledger table needs its own
  uniqueness strategy, or it passes once and fails on repeat.** `search_cache` has no `run_id` in
  its key by design (masterplan §9 — a second query in an already-explored category should be
  nearly free *across users*), and `search_credit_usage` is a real append-only table with no
  per-test cleanup. Early Phase 04 integration tests used a literal query string / provider name
  and passed in isolation, then failed the moment the same test file ran twice in a row against
  this project's long-lived local Postgres container — a stale row (or a doubled ledger sum) left
  by the first run silently changed what the second run's "first call" actually did. Fixed by
  generating a fresh `uuid4`-suffixed query/provider name per test call, the same reason
  `tests/integration/_http.py` already has `unique_root()` for the Phase 03 source cache. Re-run
  any new test file against a shared cache/ledger table twice in a row before trusting a single
  green run — the same spirit as Phase 02's "a single green run is weak evidence" for concurrency
  bugs, but for cross-invocation state instead of cross-task races.
- **GitHub's Starring-endpoint 403 (open since Phase 01) is now resolved as a permanent vendor
  restriction, not a credential gap.** `api.sources.github.GitHubRetriever.star_velocity_90d`
  calls the real endpoint and converts the 403 into `RetrieverUnavailableError`, proven against the
  real recorded 403 in `tests/fixtures/cassettes/github_api.yaml`. Since GitHub restricted
  `/stargazers` to repo admins/collaborators (2026-06-30) and fine-grained PATs are unsupported for
  it, no PAT change (including an explicit fine-grained Starring permission) can unblock it for
  competitor repos — star velocity is a permanent coverage gap; only total `stargazers_count`
  remains — see `docs/tracker.md` and `docs/external_apis.md`.
- **Never run a schema-modifying test (anything that does `alembic downgrade`/`upgrade`, e.g.
  `tests/integration/test_migrations.py`) concurrently with a long-running test against the same
  shared Postgres database.** Discovered while verifying Phase 05's live gateway checks: a
  `make check` invoked while a ~3.5-minute live test (20+10 real OpenRouter calls) was still
  running against the same `ai_pi_test` database caused `test_migrations.py`'s
  `downgrade base` / `upgrade head` cycle to drop and recreate `runs` mid-flight, deleting a row
  the live test had already inserted and was still holding a reference to — surfacing as a
  spurious `ForeignKeyViolationError` on `llm_calls.run_id` many seconds later, with a stack trace
  that looked exactly like a real application bug. Not a gateway defect: re-running the live test
  alone (no concurrent schema-touching command) passed clean. Treat any long-running Postgres-backed
  test the same as a destructive operation for scheduling purposes — never fire a second
  Postgres-writing command at the same database while one is still in flight.
- **Response-cache commit policy resolved** (Phase 05 phase doc's own open decision #2):
  `llm_response_cache` rows live in Postgres, mirroring `api.search.cache`, not committed to the
  repo as fixture files. Committing raw LLM responses would duplicate a replay mechanism this
  codebase already has one layer down — Phase 01/04's committed VCR cassettes
  (`tests/fixtures/cassettes/`, including `llm_openrouter.yaml`) already make OpenRouter traffic
  itself replayable with zero network and zero variance; a second, overlapping cache of *parsed*
  JSON would need its own freshness/commit story for no real benefit. See
  `api.llm.cache`'s module docstring.
- **`extractor_version` composition resolved** (Phase 05 phase doc's own open decision #1, deferred
  from [Phase 00](execution_phases/phase-00-foundation-contracts-ci.md)): `{prompt_version}-{model}`,
  i.e. `api.llm.prompts`' own `f"{id}@{sha256[:8]}"` plus the model id, e.g.
  `extract_claims@a1b2c3d4-deepseek/deepseek-v4-flash`. A model swap must invalidate cached
  extractions the same way an edited prompt does — the same page extracted by a different model is
  not the same claim provenance — so both components are load-bearing, not just the prompt hash.
  [Phase 06](execution_phases/phase-06-claim-extraction-span-binding.md) is the actual consumer;
  confirmed here since the phase doc explicitly asked this phase to settle the format.

- **A prompt-file placeholder meant to vary per call must live in the `## user` section, never in
  a section before `cache_prefix_ends_after`.** `api.llm.prompts.render_messages`'s system message
  (`messages[0]`) is built from `template.static_prefix` alone — a pure function of the template
  file, never substituted against `variables` (that's what makes the prefix byte-identical across
  calls and therefore cacheable, per its own docstring). Phase 11's three `synthesise_*.md` prompts
  first placed `{{repair_note}}` at the end of their `## instructions` section — inside the static
  prefix — so it was never actually substituted; the literal string `"{{repair_note}}"` sat
  unrendered in the system message on every call, identical whether or not a repair was in
  progress. Combined with `llm_response_cache`'s deterministic keying, this meant a "repair" call
  whose *user* message (findings block) was unchanged from the first attempt hashed to the same
  cache key as the original rejected response and silently replayed it — `api.synth.generate`'s
  one-repair-round logic looked correct in isolation but never actually got a second, different
  answer. Caught by `tests/integration/test_synth_generate_boundary.py`'s repair-round test, not by
  inspection. Fixed by moving `{{repair_note}}` into each prompt's `## user` section. Any future
  prompt with a "this varies on retry" placeholder needs the same check: is it after
  `cache_prefix_ends_after`, or does it just look like it will be substituted?
- **Deduplicating a batch input for cost purposes must happen before building the request to the
  vendor, not just at the cache-lookup step.** The first draft of `api.llm.embed.embed_texts`
  de-duplicated cache *lookups* (`for key in dict.fromkeys(keys): ...`) but then built the fallback
  request from `[texts[i] for i in missing_indices]`, where `missing_indices` was computed
  per-*position* in the original (non-deduplicated) input — so two identical, both-cache-miss texts
  in one call were both sent to, and billed by, the vendor, contradicting the function's own "billed
  once" docstring claim. Caught by
  `tests/integration/test_llm_embed.py::test_embed_texts_sends_only_unique_texts_to_the_vendor`,
  which inspects the literal request body — a call-count assertion alone (`transport.calls[...] ==
  1`) would have passed either way, since the bug was about *what* was sent in that one call, not
  how many calls were made. Fixed by building one `(key, representative text)` pair per still-missing
  key before ever calling the vendor.

- **A conditional `INSERT ... SELECT ... WHERE count(*) < quota` is not atomic under Postgres's
  default `READ COMMITTED`, even as one SQL statement.** `N` concurrent connections each evaluate the
  count subquery against the same pre-insert snapshot, so a quota of `N-1` first-draft-admitted `N`
  out of `N` (reproduced deterministically with real `asyncio.gather`-ed concurrent calls against real
  Postgres, not simulated). Fixed in `api.web.quota.try_create_run` with two transaction-scoped
  `pg_advisory_xact_lock`s — a fixed key for the global cap, then `hashtext(user_id)` for the per-user
  cap, always acquired in that order so no caller can deadlock — serializing the check ahead of the
  conditional insert. Any future "check then insert" quota/cap needs the same treatment; the SQL
  reading right is not evidence it's race-free — see [Phase 12](execution_phases/phase-12-api-auth-quotas.md).
- **`httpx.ASGITransport`'s default `raise_app_exceptions=True` re-raises an exception into the test
  caller even when the app's own error middleware already sent a valid response.** Starlette's
  `ServerErrorMiddleware` sends the registered 500 response *and* re-raises (intentional, for the ASGI
  server to log) — `ASGITransport` propagates that re-raise unless constructed with
  `raise_app_exceptions=False`. Needed by any test exercising a FastAPI/Starlette 500 handler through
  `ASGITransport` — see `tests/integration/test_api.py`'s `_asgi_client` helper.
- **`httpx.ASGITransport` buffers a response's entire body before returning it — it cannot observe an
  in-progress, never-ending stream.** A first-draft SSE heartbeat test read `client.stream(...)`
  against a genuinely infinite generator and hung forever, since the transport was waiting for
  `more_body: False` that would never come. Fixed by driving the ASGI callable
  (`response(scope, receive, send)`) directly with a collecting `send`, bypassing `httpx` entirely —
  the only way to test an in-progress SSE/streaming response's *contents* mid-stream in this stack.
  See `tests/integration/test_sse.py::test_heartbeat_frames_keep_a_quiet_stream_alive`.
- **A migration-added enum/status value is a "shared persistent table" hazard for
  `test_migrations.py`'s downgrade cycle, not just for cache/ledger tests.** Phase 12's `needs_input`
  `runs.status` value is invisible to a pre-Phase-12 downgrade's narrower `CHECK` constraint; a test
  that correctly created a `needs_input` row and left it in the shared, long-lived `ai_pi_test`
  database silently broke `test_migrations.py`'s full downgrade-to-`0001`-and-back-up cycle for every
  test that ran afterward, in an unrelated file, on a later invocation of the suite — not caught by a
  single green run, since the polluting test and the victim test never appear together. Any future
  phase adding a new status/enum value must either keep every value valid across every historical
  `CHECK`, or have its own tests clean up rows in the new value before they can outlive the test that
  created them — see [Phase 12](execution_phases/phase-12-api-auth-quotas.md).
- **Supabase connection spellings (empirical, refined after the first Fly deploy on 2026-08-11).**
  There are three distinct connection strings and each has its own exact query param:
  - `SUPABASE_DB_URL` — libpq form, **session pooler** host, `?sslmode=require`; used by the deploy
    workflow's `alembic upgrade head` and the keepalive/backup `psql`+`pg_dump` jobs (IPv4 CI).
  - `SUPABASE_DB_URL_ASYNC` — asyncpg form, **session pooler** host, `?ssl=require`; used by
    `api.maintenance` in keepalive.yml (IPv4 CI). asyncpg accepts the `ssl` spelling on the pooler.
  - `SUPABASE_DB_URL_ASYNC_DIRECT` — asyncpg form, **direct** host `db.<ref>.supabase.co`,
    `?sslmode=require`; used by the API and worker machines (Fly runtime). **The direct endpoint
    REJECTS asyncpg's `ssl=require` with `CantChangeRuntimeParamError: parameter "ssl" cannot be
    changed now`** (observed on the first Fly deploy); on the direct host asyncpg must use the
    `sslmode` spelling, same as libpq.
  All three are the same Supabase DB password, rotated together; `deploy/.env.prod.example`
  documents the forms.
- **Supabase direct connections are IPv6-only** (`db.<ref>.supabase.co` resolves to a single AAAA
  record), unreachable from this machine and from GitHub Actions runners; the direct connection is
  usable only from IPv6-native hosts like Fly. **Resolution (2026-08-11, refined after first Fly
  deploy): Fly machines use the DIRECT connection** (asyncpg `?sslmode=require`), while IPv4-only CI
  uses the session pooler — `aws-0-ap-south-1.pooler.supabase.com:5432` (user `postgres.<ref>`),
  which pins a server connection per client. The app must NEVER use the pooler: it caps at 15
  clients (`EMAXCONNSESSION`), and the asyncpg pool is 10 connections per machine, so two machines
  + worker crashed startup. The migration
  chain 0001→0012 was applied cleanly to the **real** Supabase project over the pooler on
  2026-08-11: 0001's `auth`-schema-exists guard skips the stub and `user_profiles` resolves its FK to
  the real `auth.users`.


## Useful Resources & References

- [`ai-product-investigator-masterplan.md`](../ai-product-investigator-masterplan.md) — the full
  product/architecture spec. Read before touching any module; it's the authority on *why*, not
  just *what*.
- [`docs/execution_phases/README.md`](execution_phases/README.md) — phase index, dependency graph,
  global conventions (test layout, definition of done, determinism rules).
- [`docs/tracker.md`](tracker.md) — living status log: what's done, open decisions, next steps.
  Check this first when resuming work after a gap.
- [`docs/external_apis.md`](external_apis.md) — measured vendor limits, costs, and go/no-go
  verdicts from Phase 01. Re-verify before deployment; nightly `tests/live/test_vendors.py`
  catches drift in the meantime.

## Contact & Questions

For questions about this project, reach out to: pulkyeet@gmail.com
