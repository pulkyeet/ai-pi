# Phase 14 — Benchmark Harness & Calibration

| | |
|---|---|
| **Depends on** | [11](phase-11-synthesis-report-assembly.md) |
| **Unlocks** | [15](phase-15-deployment-observability.md) |
| **Milestone** | No |
| **Concrete output** | Ten benchmark queries with hand-verified ground truth, a metrics report, and **every `TBD` quota knob resolved from measured data** |

---

## Objective

Measure what the system actually does, then set every number that was deliberately left blank.

## Why this phase carries unusual weight

Masterplan §10 is direct about the stakes:

> There is no public benchmark for product discovery, so this set silently becomes the definition of every quality claim in the README.

And §8.2:

> **All values are unset and must be configured before deployment.** They are deliberately left blank until benchmark runs produce real per-run search and latency numbers.

So this phase does two jobs: it establishes the quality claims, and it derives the operational numbers. Both were deferred on purpose, and this is where the deferral is paid off.

It also closes the last of masterplan §14's open items.

---

## Scope

### In

- Ten benchmark queries as checked-in YAML with dated ground truth
- Benchmark runner with cached replay
- All derived metrics
- Calibration of confidence constants, promotion thresholds, maturity thresholds, clustering threshold
- Derivation of every quota and budget value
- CI regression runs

### Out

- Changing product behaviour beyond tuning constants. If the benchmark reveals a design flaw, that is a fix in the owning phase, recorded in `docs/tracker.md` — not a patch here.

---

## Deliverables

```
bench/
├── queries/
│   ├── q01.yaml … q10.yaml
├── runner.py              # execute, score, report
├── metrics.py
└── results/               # dated result snapshots
docs/benchmark.md          # the numbers, dated
docs/tuning.md             # every constant, its value, its justification
.github/workflows/bench.yml
```

---

## Design

### The benchmark set

Masterplan §10's composition rules, which exist to prevent the set from flattering the system:

**Span difficulty deliberately** — roughly 3 easy, 4 medium, 3 hard-and-thin.

> Ten easy Western SaaS categories will produce a 95 percent recall figure that collapses on the first real query.

**Span shape, not just difficulty:**

- one dev-tools query that exercises the GitHub path
- one consumer query where GitHub should correctly be **skipped** — this tests the planner's judgement, not just its output
- one dominated by OSS incumbents
- one where the category barely exists, so the system can be checked for saying *"no real competitors"* instead of inventing four

**At least one trap:**

> a company that changed pricing recently, so a stale aggregator disagrees with the live page. Without it there is no evidence the contradiction detector ever fires rather than silently never triggering.

Format per masterplan §10:

```yaml
- id: q03
  query: "AI expense tracker for freelancers"
  difficulty: medium
  ground_truth:
    must_include: [expensify.com, ramp.com, wave.com, freshbooks.com]
    known_absent: [stripe.com]          # tests precision, not recall
    facts:
      - {entity: expensify.com, attribute: pricing.entry_usd_month,
         value: 5, verified_on: 2026-08-01}
```

`known_absent` is the precision proxy and is easy to under-use. A system that returns every vaguely-adjacent company scores perfect recall; `known_absent` is what catches it.

### Discipline

Two rules from masterplan §10 that are easy to skip and expensive to skip:

**Ground truth decays.** Date-stamp every fact and re-verify before publishing any number. A `verified_on` older than ~60 days makes the fact unusable until re-checked — enforced by the runner, which refuses to score against stale ground truth rather than quietly reporting a wrong number.

**Split six for tuning, four held out, touched only at the end.**

> Without this the README number is fiction told to yourself.

The four held-out queries are run **once**, after all tuning is complete. If the held-out numbers are much worse than the tuning numbers, the constants were overfitted and that is the finding — reported honestly rather than tuned away by peeking.

### Metrics

Masterplan §10's derived metrics, each with a defined computation:

| Metric | Computation |
|---|---|
| Competitor recall | \|found ∩ must_include\| / \|must_include\| |
| Precision proxy | 1 − (\|found ∩ known_absent\| / \|known_absent\|) |
| Fact accuracy | matching claims / ground-truth facts, within per-attribute tolerance |
| **Sentence binding rate** | bound sentences / total prose sentences — **must be 100%** |
| Contradiction firing rate | runs where ≥1 contradiction detected; **must be ≥1 on the trap query** |
| Cost per run | mean and p95, split LLM vs search |
| Latency | p50 and p95 wall clock |
| Cache hit rate | source, search, extraction, separately |
| Coverage | mean, plus which branches fail most often |
| Extraction drop rate | by drop reason ([Phase 06](phase-06-claim-extraction-span-binding.md)) |
| Planner fallback rate | how often planning fell back ([Phase 09](phase-09-interpreter-planner.md)) |
| Synthesis rejection rate | how often MVP/gaps/risks were rejected ([Phase 11](phase-11-synthesis-report-assembly.md)) |

The last three are process metrics rather than quality metrics, and they are the ones that explain *why* a quality number moved. A recall drop with a simultaneous planner-fallback spike is a completely different problem from a recall drop with a stable fallback rate.

Sentence binding rate is the only metric with a hard pass condition: anything below 100% means [Phase 11](phase-11-synthesis-report-assembly.md)'s enforcement is broken, and it is a bug, not a tuning target.

### Calibration

Tune only against the six tuning queries. Each constant gets a recorded justification in `docs/tuning.md` — a number without a reason is a number nobody can revisit later.

| Constant | From | Tuned against |
|---|---|---|
| `BASE` grades, multi-domain step, decay rate, contradiction penalty | [08](phase-08-grading-confidence-contradictions.md) | Correlation between confidence and fact accuracy — high-confidence claims should be right more often, which is the only thing "calibrated" can mean here |
| Promotion thresholds (5 comments / 3 threads; reaction weights) | [08](phase-08-grading-confidence-contradictions.md) | Findings that survive vs hand-judged real pain points |
| Maturity tier thresholds | [07](phase-07-entity-resolution.md) | Hand-labelled entities from the benchmark set |
| Clustering similarity threshold | [11](phase-11-synthesis-report-assembly.md) | Hand-labelled complaint clusters; over-merge penalised harder than under-merge |

### Deriving the quota knobs

The masterplan's blanks, now filled from measurement:

```python
RUNS_PER_USER_PER_DAY    = # abuse-tolerance judgement, informed by cost/run
GLOBAL_RUNS_PER_DAY      = # DERIVED, see below
MAX_CONCURRENT_RUNS      = # from p95 latency and vendor rate limits
RUN_BUDGET_WEIGHT        = # p95 observed weight × 1.5 headroom
RUN_BUDGET_USD           = # p95 observed cost × 3  (bug insurance, not cost control)
RUN_TIMEOUT_S            = # p95 latency × 2
MAX_COMPETITORS_PROFILED = # where recall stops improving per unit cost
MAX_PAGES_PER_ENTITY     = # same
MAX_COMMUNITY_THREADS    = # where finding quality stops improving
```

The one that matters is derived, not guessed (masterplan §8.2):

```
GLOBAL_RUNS_PER_DAY ≈ (monthly_search_credits / 30) / searches_per_run_p95
```

**p95, not median** — the tail is what drains an allowance.

Two things push the effective number up, and both are measured here rather than assumed: source and search caches are shared across users, so a second query in an already-explored category is close to free; and cache hit rate rises as the corpus grows.

`RUN_BUDGET_USD` at 3× p95 deserves its comment: the cap exists to protect against **your own bugs** — a retry storm or a fan-out to 200 profile tasks — not against token cost. Setting it tight would trip on legitimate expensive runs; setting it at 3× catches runaway behaviour while never touching a normal run.

`MAX_COMPETITORS_PROFILED` is found by sweeping it across the tuning queries and plotting recall against cost. There is a knee; profiling past it buys nothing.

### CI regression

Nightly, against cached responses ([Phase 05](phase-05-llm-gateway.md), [Phase 06](phase-06-claim-extraction-span-binding.md)), temperature 0 — free and deterministic per masterplan §11. Fails on: sentence binding below 100%, recall dropping more than 10 points from baseline, cost per run rising more than 50%, or contradiction firing rate reaching zero.

Cost regression as a *test failure* is unusual and worth keeping. A silently broken prompt cache ([Phase 05](phase-05-llm-gateway.md)) shows up as a 4× cost increase with no behavioural symptom at all — the only way to notice is to assert on it.

---

## Testing

| Kind | Test | Asserts |
|---|---|---|
| Unit | Each metric computed correctly from synthetic run data | Metrics are right before conclusions are drawn from them |
| Unit | Ground-truth loader rejects facts older than the staleness window | Decay discipline enforced by the tool |
| Unit | Recall, precision proxy, fact accuracy on hand-worked examples | Arithmetic |
| Integration | Runner executes a query from cache, produces a scored result | Replay works |
| Integration | Runner refuses to score against stale ground truth | Discipline is not optional |
| Integration | Held-out queries inaccessible during tuning mode | Overfitting guard is mechanical, not a promise |
| Integration | Full benchmark from cache completes at zero API spend | Free iteration |
| Live | Full benchmark against real APIs, monthly | Cassettes and ground truth both re-verified |

---

## Exit criteria

- [ ] Ten queries checked in, matching the difficulty **and** shape composition rules
- [ ] At least one trap query with a recent pricing change
- [ ] All ground truth hand-verified and date-stamped
- [ ] Six tuning / four held-out split enforced mechanically
- [ ] Runner executes from cache at zero spend
- [ ] All metrics computed and recorded in `docs/benchmark.md`, dated
- [ ] **Sentence binding rate is 100%**
- [ ] **Contradiction detector fires on the trap query**
- [ ] Thin-category query returns few/zero competitors, not invented ones
- [ ] Consumer query correctly skips GitHub; dev-tools query uses it
- [ ] All four constant groups calibrated, each with a recorded justification
- [ ] **Every `TBD` in masterplan §8.2 replaced with a derived value**
- [ ] `GLOBAL_RUNS_PER_DAY` derived from p95 search count, not guessed
- [ ] Held-out set run **once**, after tuning; results reported honestly
- [ ] CI regression job runs nightly with the four failure conditions
- [ ] Masterplan §14 open items #1 and #2 closed

---

## Risks

| Risk | Mitigation |
|---|---|
| Benchmark flatters the system | Composition rules are the mitigation and they are non-negotiable: 3 hard/thin queries, `known_absent` precision proxy, held-out split. |
| Constants overfitted to six queries | Held-out set, run once, at the end. A gap between tuning and held-out numbers is reported, not tuned away. |
| Ground truth rots and numbers become fiction | Date stamps plus a runner that refuses stale facts. Monthly live re-verification. |
| Tuning constants to hit a number rather than to be right | Every constant needs a *reason* in `docs/tuning.md`, not just a value. "It improved recall by 3 points" is not a reason on its own. |
| Recall is genuinely poor | Then that is the finding, and it is reported. The likely levers are discovery seeds ([Phase 10](phase-10-task-handlers-e2e.md)) and search provider choice ([Phase 04](phase-04-search-domain-retrievers.md)) — both measurable, both fixable in their owning phase. |
| Quota values too generous, allowance drained | p95-based derivation plus the kill switch ([Phase 12](phase-12-api-auth-quotas.md)). Failure mode is a day of read-only, not a bill. |
| Cost per run far above model | Diagnosable from the cost split and cache rates. Most likely causes are already individually instrumented. |

## Open decisions

1. **Publish the numbers, including the bad ones?** Strong argument for yes: a README that says "68% recall on hard categories, 94% on mainstream, here is the benchmark set" is far more credible than an unqualified 95%, and it is the honest version of a claim that this set silently defines anyway. Recommend publishing the full table with the methodology.
2. **Should the benchmark set grow past ten?** Ten is enough to define the quality claims and small enough to hand-verify properly. Growing it dilutes verification effort, which is the scarce resource. Recommend keeping ten and re-verifying well rather than adding more.
