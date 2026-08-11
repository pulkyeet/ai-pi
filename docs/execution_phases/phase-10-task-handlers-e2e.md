# Phase 10 — Task Handlers & End-to-End Run

| | |
|---|---|
| **Depends on** | [02](phase-02-executor-core.md), [04](phase-04-search-domain-retrievers.md), [06](phase-06-claim-extraction-span-binding.md), [07](phase-07-entity-resolution.md), [09](phase-09-interpreter-planner.md) |
| **Unlocks** | [11](phase-11-synthesis-report-assembly.md) |
| **Milestone** | ⭐ **Walking skeleton** |
| **Concrete output** | `python -m api.cli run "AI expense tracker for freelancers"` → real entities and span-bound claims in Postgres, with live event output and a cost figure |

---

## Objective

Wire seven independently-proven components into a pipeline that actually runs a real query against the real internet and produces real evidence.

## Why this is the milestone

Everything before this is a component with tests. This is the first time the system *does the thing*. It is also where integration problems surface — and by design, they surface against a foundation where each piece has already been proven correct in isolation, so a failure here is almost always a wiring problem rather than a logic problem.

No report yet: this phase produces claims and entities in the database. Turning those into prose is [Phase 11](phase-11-synthesis-report-assembly.md). Splitting them means integration risk and synthesis risk are debugged separately rather than together.

---

## Scope

### In

- One handler per `TaskKind` in the registry
- Handler registration and dispatch through [Phase 02](phase-02-executor-core.md)'s protocol
- Dynamic fan-out: discovery spawns profiling tasks
- A CLI that runs a query end to end and streams events
- Real-run instrumentation: cost, latency, search count, cache rates, drop rate

### Out

- Findings, synthesis, report assembly ([Phase 11](phase-11-synthesis-report-assembly.md))
- HTTP, auth, quotas ([Phase 12](phase-12-api-auth-quotas.md))
- Any UI ([Phase 13](phase-13-frontend.md))
- Quality tuning — measure first, tune in [Phase 14](phase-14-benchmark-calibration.md)

---

## Deliverables

```
src/api/tasks/
├── __init__.py
├── registry.py            # kind -> handler binding
├── discover.py            # discover_competitors
├── profile.py             # profile_product
├── pricing.py             # extract_pricing
├── community.py           # mine_community
├── oss.py                 # oss_profile
├── funding.py             # find_funding
└── trends.py              # trend_signals
src/api/cli.py             # run, inspect, replay
tests/integration/test_handlers.py
tests/integration/test_pipeline_e2e.py
tests/live/test_real_run.py
```

---

## Design

### Handler shape

Every handler implements [Phase 02](phase-02-executor-core.md)'s protocol and follows the same shape:

```
retrieve  →  resolve entities  →  extract claims  →  return HandlerResult
```

Handlers are thin. All the difficult logic lives in the layers below — a handler that contains interesting code is a signal that something belongs in [Phase 04](phase-04-search-domain-retrievers.md), [Phase 06](phase-06-claim-extraction-span-binding.md), or [Phase 07](phase-07-entity-resolution.md) instead. This keeps the phase's integration surface small, which is the point.

Every handler must be safe to run twice — a re-queued task after lease expiry re-executes it. Safety comes from the layers: caches make retrieval idempotent, `ON CONFLICT DO NOTHING` makes claim writes idempotent, entity upsert is idempotent. Handlers add no state of their own.

### The handlers

**`discover_competitors`** — the fan-out root, and the one with real judgement in it.

1. Generate query variants from the brief (category, segment, geography, keywords)
2. Search via [Phase 04](phase-04-search-domain-retrievers.md), budget-bounded
3. Also pull structured seeds: AlternativeTo competitor graph, `awesome-<category>` GitHub repos (very high precision per masterplan §5), package registry search where the category is developer-facing
4. Resolve every candidate through [Phase 07](phase-07-entity-resolution.md) — **artifact verification drops hallucinated competitors here**
5. Rank surviving candidates and spawn `profile_product` children up to `MAX_COMPETITORS_PROFILED`

Ranking matters because the profile budget is finite. Signals: appears in multiple independent result sets, has its own registrable domain, non-`hobby` maturity, search rank. Deliberately simple and deterministic — a model ranking competitors would be a new hallucination surface with no artifact check behind it.

**`profile_product`** — path-guess `/pricing`, `/plans`, `/docs`, `/changelog` for the entity's domain; fetch the homepage; extract claims from each. Almost no searching, per masterplan §7. This handler is where the path-guessing cost saving is realised.

**`extract_pricing`** — narrower and cheaper than `profile_product`: pricing paths only. The planner uses it when it wants pricing for many entities without full profiles.

**`mine_community`** — HN Algolia search, GitHub issues by reactions, and Stack Exchange. Emits `complaint.<theme>` and `request.<theme>` claims. Bounded by `MAX_COMMUNITY_THREADS`. (Reddit was planned here but dropped per [D5](README.md#deviations-from-the-masterplan) — no Reddit integration ships.)

Themes are assigned by the extraction model from the closed parameterised family ([Phase 00](phase-00-foundation-contracts-ci.md)) — so themes are open in slug but closed in shape, and near-duplicate theme clustering is deferred to [Phase 11](phase-11-synthesis-report-assembly.md).

**`oss_profile`** — GitHub API: stars, 90-day velocity, last commit, license, contributors. All grade A structured data. Planner-gated per masterplan §5.

**`find_funding`** — the hard one. Masterplan §5 is blunt: *Crunchbase is effectively paywalled. Coverage will be patchy. Report it as a coverage gap rather than guessing.* Substitutes: SEC EDGAR full-text and Form D (US, real numbers), UK Companies House, Wikidata, OpenCorporates. This handler is **expected to fail often**, and failing is correct behaviour — it reduces coverage and the report says so.

**`trend_signals`** — Wikipedia pageviews, plus HN post volume over time computed from search results already in the pipeline. Masterplan §5 notes this beats pytrends, which breaks constantly.

### The CLI

```bash
python -m api.cli run "AI expense tracker for freelancers"
python -m api.cli run "..." --budget 20 --no-cache
python -m api.cli inspect <run_id>      # tasks, claims, entities, costs
python -m api.cli replay <run_id>       # re-run from caches, zero spend
```

`run` streams events to the terminal as they arrive — the same event stream [Phase 13](phase-13-frontend.md) will render as a live checklist. Getting it working in a terminal first means the stream is proven before any UI depends on it.

`inspect` is the debugging surface the masterplan promises: *"Debugging is a SQL query against a task table"* (§12.1). It prints the DAG with per-task status, timing, cost, error; claims grouped by entity and attribute; and the drop-rate breakdown.

`replay` re-runs entirely from the source, search and extraction caches. Free, deterministic, and the mechanism [Phase 14](phase-14-benchmark-calibration.md) depends on for cheap iteration.

### Instrumentation

Every real run records, and the CLI prints:

| Metric | Why it matters |
|---|---|
| Wall-clock duration | The masterplan promises under three minutes |
| Total cost, split LLM vs search | Validates the ~$0.04/run model |
| Search count (and p95 across runs) | Feeds `GLOBAL_RUNS_PER_DAY` derivation |
| Cache hit rates: source, search, extraction | The three cost levers |
| Claims extracted / dropped, by drop reason | Extraction health |
| Entities discovered / verified / rejected | Rule 2 in action |
| Tasks done / failed / skipped, and coverage | Partial-failure behaviour |

These are the raw inputs to every [Phase 14](phase-14-benchmark-calibration.md) decision. Collecting them from the first real run means the calibration phase has history to work with rather than starting cold.

---

## Testing

| Kind | Test | Asserts |
|---|---|---|
| Integration | Each handler in isolation against cassettes | Handler contracts, offline |
| Integration | `discover_competitors` fan-out spawns children up to the cap, not beyond | Bounded fan-out |
| Integration | Hallucinated candidate (404 domain) dropped by verification and counted | Rule 2 end to end |
| Integration | Handler idempotence — run twice, claim count unchanged | Lease re-queue safety |
| Integration | Handler failure is contained: one dead branch, run completes, coverage reduced | Partial failure is normal |
| Integration | **Full pipeline from cassettes** — query → brief → plan → execute → claims in DB | The walking skeleton, offline and deterministic |
| Integration | Budget exhaustion mid-run: remaining tasks skipped, run completes, skipped ≠ failed | Budget semantics |
| Integration | Every claim in the DB after a full run has a verified span | The core guarantee survives integration |
| Integration | Every entity in the DB has a verified artifact | Rule 2 survives integration |
| Integration | Event stream ordering across concurrent tasks is causally consistent | [Phase 13](phase-13-frontend.md) can rely on it |
| Live | **Real run** on 3 queries: one mainstream, one dev-tools, one thin | It actually works |
| Live | Real run completes within `RUN_TIMEOUT_S` and under budget | The three-minute promise |
| Live | Thin category produces few or zero competitors — **not four invented ones** | The masterplan's explicit anti-goal |

The thin-category live test is worth calling out. Masterplan §10 requires a benchmark query "where the category barely exists so the system can be checked for saying *no real competitors* instead of inventing four". Artifact verification ([Phase 07](phase-07-entity-resolution.md)) should make invention structurally impossible — this test confirms the structure holds under real conditions rather than only in unit tests.

---

## Exit criteria

- [ ] All seven handlers implemented and registered
- [ ] `api.cli run "<query>"` completes end to end against the real internet
- [ ] Full-pipeline integration test passes offline from cassettes
- [ ] Real run on 3 queries produces plausible entities and claims
- [ ] **Every claim in the DB after a real run has a verified span** — asserted, not assumed
- [ ] **Every entity has a verified public artifact**
- [ ] Thin-category query yields few/zero competitors rather than invented ones
- [ ] Fan-out bounded; budget exhaustion skips rather than fails
- [ ] One dead branch does not fail the run; coverage reflects it
- [ ] Handlers idempotent under re-execution
- [ ] `inspect` and `replay` both work
- [ ] All instrumentation metrics recorded and printed
- [ ] Measured cost per run recorded and compared to the ~$0.04 model
- [ ] Measured p95 search count per run recorded (input to [Phase 14](phase-14-benchmark-calibration.md))
- [ ] Coverage ≥ 80% on `src/api/tasks/` (lower bar — these are thin adapters, and the real assurance is the pipeline test)

---

## Risks

| Risk | Mitigation |
|---|---|
| Integration reveals a contract mismatch between phases | Expected, and cheap: each component has its own tests, so a mismatch localises fast. Contract changes get recorded in `docs/tracker.md`. |
| Run exceeds three minutes | Measured here for the first time. Levers, in order: raise concurrency, cut `MAX_COMPETITORS_PROFILED`, cut `MAX_PAGES_PER_ENTITY`. Tuned in [Phase 14](phase-14-benchmark-calibration.md). |
| Cost far above $0.04 | Diagnosable by construction — cost is split LLM vs search, and cache hit rates are reported. The likely culprits are a broken prompt cache ([Phase 05](phase-05-llm-gateway.md)) or a low path-guess hit rate ([Phase 03](phase-03-fetch-source-cache.md)), both individually measured. |
| Discovery recall poor | Multiple seed strategies (search, AlternativeTo, `awesome-` repos, package registries) rather than search alone. Measured properly in [Phase 14](phase-14-benchmark-calibration.md). |
| `find_funding` almost always fails | Anticipated by the masterplan. It reduces coverage and the report says so. Not a bug. |
| Handlers accumulate logic and become untestable | Explicit design rule: handlers are thin. Reviewed at exit — any handler with substantial branching indicates logic that belongs a layer down. |
| Live tests flake on vendor availability | Live tests are nightly and non-blocking. The cassette-based pipeline test is the gate. |

## Open decisions

1. **Competitor ranking signals.** Currently simple and deterministic. If [Phase 14](phase-14-benchmark-calibration.md) shows recall is limited by ranking the wrong candidates into the profile budget, add signals — but keep it deterministic. A model ranking competitors reintroduces exactly the hallucination surface artifact verification exists to close.
2. **Should `mine_community` run before profiling?** Community results sometimes name competitors the search missed, which would improve discovery. Costs a serialisation point in the DAG. Measure whether community-sourced discovery adds unique entities before adding the dependency edge.
