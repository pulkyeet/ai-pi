# Phase 08 — Grading, Confidence & Contradictions

| | |
|---|---|
| **Depends on** | [06](phase-06-claim-extraction-span-binding.md), [07](phase-07-entity-resolution.md) |
| **Unlocks** | [11](phase-11-synthesis-report-assembly.md) |
| **Milestone** | No |
| **Concrete output** | Deterministic confidence on every claim, and a contradiction detector proven to fire on the masterplan's trap case (live pricing page vs stale aggregator) |

---

## Objective

Turn a pile of claims into graded, scored evidence — and surface disagreement between sources instead of silently picking a winner.

## Why this phase is entirely deterministic

Masterplan §12.5 and §12.6 are two of the strongest decisions in the plan, and this phase is where they become code:

> **Confidence is computed, not generated.** `0.82` emitted by an LLM is decoration, and the first competent interviewer asks what it was calibrated against. A formula over grade, source count, age and contradiction status is deterministic, tunable, and defensible.

> **Contradiction detection is SQL.** It is a `GROUP BY`. It is free, deterministic, cannot hallucinate a contradiction, and cannot miss one.

**No model is called in this phase.** That is the point. Everything here is arithmetic and SQL over typed claims, which is only possible because the claim vocabulary is closed ([Phase 00](phase-00-foundation-contracts-ci.md)) — the closed vocabulary's entire justification is that it makes this phase mechanical.

---

## Scope

### In

- Source-type → grade assignment (A/B/C/D)
- The confidence formula
- Contradiction detection and resolution
- Loser retention and surfacing
- Promotion thresholds turning anecdotes into findings
- Coverage computation

### Out

- Any LLM call
- Finding *statements* — prose generation is [Phase 11](phase-11-synthesis-report-assembly.md)
- Complaint near-duplicate clustering via pgvector ([Phase 11](phase-11-synthesis-report-assembly.md))

---

## Deliverables

```
src/api/evidence/
├── __init__.py
├── grade.py           # source type -> grade
├── confidence.py      # the formula
├── contradictions.py  # detection + resolution SQL
├── promotion.py       # anecdote -> finding thresholds
└── coverage.py        # coverage scoring
tests/unit/, tests/integration/
```

---

## Design

### Grading

Masterplan §4.6's table, applied by **retrieval provenance**, not by model judgement:

| Grade | Source type | Examples |
|---|---|---|
| A | structured primary | pricing page, API docs, GitHub API, package registry counts |
| B | prose primary | changelog, company blog, Wayback snapshot of a primary |
| C | third-party aggregator | G2, Capterra, Crunchbase, StackShare |
| D | community anecdote | one Reddit or HN comment, one GitHub issue |

Grade is a pure function of `(retrieval_reason, source domain, entity relationship)` — all known at fetch time from [Phase 03](phase-03-fetch-source-cache.md) and [Phase 04](phase-04-search-domain-retrievers.md). It is assigned mechanically:

- A page fetched from the entity's *own* registrable domain via path guessing → A
- The same domain's `/blog` or `/changelog` → B
- A known aggregator domain → C
- HN Algolia comment, GitHub issue body, Stack Exchange answer → D
- Wayback snapshot → inherits the grade the original would have had, capped at B

The aggregator and own-domain determinations use the entity keys from [Phase 07](phase-07-entity-resolution.md), which is why this phase depends on it.

### The confidence formula

Masterplan §4.6, implemented verbatim:

```python
BASE = {"A": 0.90, "B": 0.75, "C": 0.55, "D": 0.35}

def confidence(best_grade, n_distinct_domains, age_days, contradicted):
    base    = BASE[best_grade]
    multi   = 1 + 0.05 * min(n_distinct_domains - 1, 4)
    decay   = 0.98 ** (age_days / 30)
    penalty = 0.6 if contradicted else 1.0
    return min(base * multi * decay * penalty, 0.97)
```

Four properties worth stating explicitly, because they are what make it defensible in a conversation:

- **Deterministic** — same inputs, same output, always. Reproducible by hand.
- **Tunable** — five numbers, each with an argument behind it, all adjustable in [Phase 14](phase-14-benchmark-calibration.md) against benchmark ground truth.
- **Explainable** — the report can show the inputs, so a user can see *why* something scored 0.61.
- **Capped below 1.0** — the system never claims certainty.

`n_distinct_domains` counts distinct **registrable domains**, not distinct sources. Three pages on the same site are one domain — corroboration means independent sources, and this is where a naive implementation would silently inflate every score.

`age_days` derives from `as_of` when the claim carries one, else `fetched_at`. A claim from a page fetched today about pricing stated in 2024 is old evidence, not fresh evidence, and `as_of` is what captures that.

The formula's inputs are **stored alongside the result**, so confidence is auditable after the fact and re-computable if the formula is tuned.

### Contradiction detection

Masterplan §4.7, the query that justifies the closed vocabulary:

```sql
select entity_id, attribute, array_agg(distinct value_num), count(*)
  from claims
 where run_id = $1
   and superseded_by is null
   and grade in ('A','B','C')
 group by entity_id, attribute
having count(distinct value_num) > 1;
```

Grade D is excluded — two Reddit comments disagreeing about a price is not a contradiction, it is noise.

**Resolution:** highest grade wins; ties broken by most recent `as_of`. The loser is **retained and surfaced**, not deleted:

> "pricing page says $5 last week, a 2025 review says $18" is genuinely useful signal, not noise to be swept up.

The loser gets `superseded_by` set to the winner's id — it stays queryable, it appears in the report's `contradictions` array with both values, both grades, and both dates, and it applies the 0.6 confidence penalty to the winner. A resolved contradiction still lowers confidence, because the existence of disagreement is itself evidence of uncertainty.

Numeric attributes compare on `value_num` with a tolerance (floating point, and `$5.00` vs `$5` should not be a contradiction). Text and enum attributes compare on normalised `value_text`. The comparison rule is per-attribute, driven by `ATTRIBUTE_SPEC` from [Phase 00](phase-00-foundation-contracts-ci.md).

### Promotion thresholds

Masterplan §4.6 — how anecdotes become findings:

- **Reddit and HN themes:** at least **5 supporting comments across at least 3 distinct threads**
- **GitHub issues:** **reaction-weighted instead**, so one issue with 47 thumbs-up clears the bar where one Reddit comment never does

The two rules differ deliberately. Comment volume is a weak signal that needs breadth (distinct threads) to mean anything; reaction counts are an explicit vote and carry more weight per unit.

**The report prints the real N.** No invented "3,248 comments". `support_count` and `distinct_threads` are both stored on the finding and both rendered.

### Coverage

Masterplan Rule 4: *`coverage` is reported separately from confidence. A run whose funding branch died says so, out loud, on the report.*

Coverage is computed from planned-versus-completed task branches:

```
coverage.score = (completed_weight) / (planned_weight)
coverage.failed_branches = [kinds where every task failed or was skipped]
```

Weighted by `cost_weight`, so a dead `mine_community` branch (weight 4) costs more coverage than a dead `extract_pricing` (weight 1) — losing the expensive branch means losing more of the intended research.

Budget-skipped and failed are both counted as incomplete but recorded distinctly, so the report can say "funding branch failed" versus "funding branch skipped for budget" — different messages to the user, and the masterplan's insistence on saying so out loud means the distinction should survive to the surface.

Contributing signals also include `insufficient_signal` entities from [Phase 07](phase-07-entity-resolution.md), so a run that found competitors but could not classify any of them reports reduced coverage rather than false confidence.

---

## Testing

| Kind | Test | Asserts |
|---|---|---|
| Unit | Confidence: one case per grade at baseline | Base values correct |
| Unit | Multi-domain multiplier: 1, 2, 3, 5, 10 domains — caps at 4 increments | `min(n-1, 4)` bound |
| Unit | **Distinct domains, not distinct sources** — 3 pages on one domain scores as 1 | The inflation trap |
| Unit | Age decay at 0, 30, 90, 365, 1000 days | Monotonic decrease |
| Unit | Contradiction penalty applies exactly 0.6 | Penalty |
| Unit | Cap at 0.97 never exceeded, including A-grade + 5 domains + fresh | Never claims certainty |
| Unit (property) | Confidence ∈ (0, 0.97] for all valid inputs | Bounded |
| Unit (property) | Monotonic in each input independently (more domains ↑, older ↓, contradicted ↓) | Formula behaves sensibly |
| Unit | Grade assignment table: one case per source type, including Wayback cap at B | Mechanical grading |
| Unit | Promotion: 4 comments / 3 threads fails; 5 / 3 passes; 5 / 2 fails | Both conditions required |
| Unit | GitHub reaction weighting clears with one high-reaction issue | Different rule, correctly different |
| Unit | Coverage arithmetic weighted by `cost_weight`; skipped vs failed distinguished | Honest reporting |
| Integration | Contradiction SQL on seeded claims: two prices for one entity → detected | Core query |
| Integration | Grade D excluded from detection | Noise filtered |
| Integration | Resolution picks highest grade; tie broken by recency | Deterministic winner |
| Integration | **Loser retained, `superseded_by` set, appears in report data** | The valuable half |
| Integration | Numeric tolerance: `$5.00` vs `$5` is not a contradiction; `$5` vs `$18` is | No false positives on formatting |
| Integration | **The trap case, end to end** — a live pricing page at grade A saying $5 and a 2025 aggregator at grade C saying $18 → contradiction detected, A wins, C retained, penalty applied | The masterplan's named scenario |

That final test is the phase's signature. The masterplan requires at least one benchmark query to include a company that changed pricing recently, precisely so the contradiction detector is proven to fire rather than silently never triggering. It is built here as an integration test with seeded data, and re-proven in [Phase 14](phase-14-benchmark-calibration.md) against the real benchmark.

---

## Exit criteria

- [ ] Confidence formula implemented exactly as masterplan §4.6
- [ ] Both confidence property tests pass (bounded, monotonic)
- [ ] Distinct-**domain** counting proven, not distinct-source
- [ ] Formula inputs stored per claim for auditability and recomputation
- [ ] Grade assigned mechanically from retrieval provenance; no model involved
- [ ] Contradiction SQL detects; grade D excluded
- [ ] Resolution deterministic; losers retained with `superseded_by`
- [ ] **Trap-case integration test passes**
- [ ] Numeric tolerance prevents formatting false positives
- [ ] Promotion thresholds implemented; real N stored and surfaced
- [ ] Coverage weighted by `cost_weight`; skipped vs failed distinguished
- [ ] **Zero LLM calls in this phase** — enforced by a test asserting the gateway is never invoked
- [ ] Coverage ≥ 90% on `src/api/evidence/`

---

## Risks

| Risk | Mitigation |
|---|---|
| Formula constants are arbitrary | They are — and they are *visibly* arbitrary, which is the point versus a model's opaque 0.82. [Phase 14](phase-14-benchmark-calibration.md) tunes them against ground truth; stored inputs allow recomputation without re-running extraction. |
| Contradiction detector never fires in practice | Exactly why the masterplan mandates a trap query in the benchmark set. Firing rate is a reported metric in [Phase 14](phase-14-benchmark-calibration.md), not an assumption. |
| False-positive contradictions from formatting | Per-attribute comparison rules with numeric tolerance; tested explicitly. |
| Distinct-domain counting inflated by CDN or subdomain variation | Uses [Phase 07](phase-07-entity-resolution.md)'s registrable-domain logic, so `docs.foo.com` and `www.foo.com` are one domain. |
| Promotion thresholds too strict, findings starve | Measured in [Phase 14](phase-14-benchmark-calibration.md). Tunable. Note the failure direction is safe: too strict means fewer findings, not wrong findings. |
| Coverage score is gameable or meaningless | Weighted by planned cost, computed from the actual DAG, not self-reported. A run that plans little cannot score high coverage — planned weight is the denominator. |

## Open decisions

1. **Should contradiction penalty apply to the winner, the loser, or both?** Currently the winner (the loser is superseded and not scored for display). Argument for both: if a contradiction is surfaced, both values shown should carry appropriate uncertainty. Decide when [Phase 13](phase-13-frontend.md) settles how contradictions render.
2. **Cross-run contradiction detection.** The query is scoped to `run_id`. Detecting that *this* run disagrees with *last month's* run on the same entity would be a genuinely interesting feature — and is out of scope for v1. Noted, not built.
