# Phase 11 — Findings, Constrained Synthesis & Report Assembly

| | |
|---|---|
| **Depends on** | [08](phase-08-grading-confidence-contradictions.md), [10](phase-10-task-handlers-e2e.md) |
| **Unlocks** | [12](phase-12-api-auth-quotas.md), [14](phase-14-benchmark-calibration.md) |
| **Milestone** | ⭐ **Product complete** |
| **Concrete output** | A `Report` matching the masterplan §2 contract exactly, with **100% sentence binding** — every prose sentence carries at least one `claim_id`, verified programmatically |

---

## Objective

Turn graded claims into a report a founder can act on, without a single unbindable sentence surviving to the output.

## The rule this phase enforces

Masterplan Rule 1:

> **Every prose sentence carries at least one `claim_id`.** Sentences that cannot be bound are dropped, not flagged.

Dropped, not flagged. A sentence with a "citation needed" marker is a worse product than no sentence — it pushes verification onto the user, which is the work the product exists to do. This phase drops.

And the guard against the category's standard failure mode, masterplan §4.9:

> The MVP, gap and risk generators receive **only the resolved finding set**, never raw page text. […] Validation rejects output referencing fewer than three findings, or zero findings from the complaints category. This is the guard against generic advice.

---

## Scope

### In

- Claims → findings, with promotion thresholds applied
- Complaint near-duplicate clustering via pgvector
- Constrained synthesis: MVP statement, feature gaps, risks
- Sentence-level citation binding and enforcement
- Report assembly against the §2 contract
- Freshness and coverage reporting

### Out

- Serving the report over HTTP ([Phase 12](phase-12-api-auth-quotas.md))
- Rendering ([Phase 13](phase-13-frontend.md))
- Quality measurement ([Phase 14](phase-14-benchmark-calibration.md))

---

## Deliverables

```
src/api/synth/
├── __init__.py
├── findings.py        # claims -> findings
├── cluster.py         # pgvector complaint dedup
├── generate.py        # constrained MVP / gaps / risks
├── bind.py            # sentence-level citation enforcement
└── assemble.py        # Report construction
src/api/prompts/synthesise_mvp.md
src/api/prompts/synthesise_gaps.md
src/api/prompts/synthesise_risks.md
tests/unit/, tests/integration/
```

---

## Design

### Claims → findings

`findings` is the only table whose text ever reaches a user, and it always carries `claim_ids` (masterplan §4.3). That single constraint is the entire drill-down mechanism, and [Phase 00](phase-00-foundation-contracts-ci.md) already made it a database CHECK — a finding with no citations cannot be inserted.

Findings are produced per kind:

| Kind | Built from | Threshold |
|---|---|---|
| `pain_point` | `complaint.<theme>` claims, clustered | ≥5 comments across ≥3 threads; or reaction-weighted for GitHub ([Phase 08](phase-08-grading-confidence-contradictions.md)) |
| `feature_gap` | `request.<theme>` claims + absence of matching `feature.<slug>.present` | Same promotion rules |
| `pricing_observation` | `pricing.*` claims across entities | ≥2 entities with pricing |
| `competitor` | resolved entities with ≥1 claim | Artifact verified ([Phase 07](phase-07-entity-resolution.md)) |

Finding statements at this stage are **templated, not generated** — `"{n} users across {t} threads report {theme}"`. Deterministic, trivially bindable, and no model involved. Prose generation is reserved for the three genuinely generative outputs below.

### Complaint clustering

The one place pgvector is used — masterplan §11: *"pgvector only for complaint near duplicate detection. Not the primary retrieval path."*

`complaint.<theme>` slugs arrive from extraction with open slugs, so `receipt-ocr-accuracy` and `ocr-misreads-receipts` are the same complaint under different names. Without clustering, both fall below the promotion threshold and a real pain point is lost.

Embed theme slugs plus a representative quote, cluster by cosine similarity above a threshold, take the most frequent slug as the cluster label, and sum support across the cluster. `support_count` and `distinct_threads` are computed **after** clustering — which is exactly why clustering must happen before promotion.

Threshold is a tunable constant, calibrated in [Phase 14](phase-14-benchmark-calibration.md) against hand-labelled clusters. Over-merging is the more damaging error (it conflates distinct complaints), so it starts conservative.

### Constrained synthesis

Three generated outputs: MVP statement, feature gaps, risks. Each obeys masterplan §4.9:

**Input is the resolved finding set only.** Never raw page text. The prompt receives a list of `(finding_id, kind, statement, support_count, confidence)`. No page content, no quotes, no URLs. This is both the anti-generic-advice guard and a second injection boundary — page text reached a model once, under closed-vocabulary constraint, in [Phase 06](phase-06-claim-extraction-span-binding.md), and never again.

**Output must declare `addresses_finding_ids`.** Validation rejects output that references fewer than three findings, or zero findings from the complaints category. Both conditions, per the masterplan.

The complaints requirement is the sharper of the two: an MVP proposal that addresses no user complaint is definitionally generic advice, since it was not derived from anything users said. Rejecting it mechanically is what makes the guard real rather than aspirational.

**Rejection handling:** one repair attempt with the specific violation, then the section is **omitted from the report** and recorded as a coverage gap. A report with no MVP statement is honest; a report with a generic MVP statement is the failure mode the whole product exists to avoid.

### Sentence-level binding

The final gate, and the most mechanical:

```
for each generated section:
    split into sentences
    for each sentence:
        resolve its cited finding_ids -> claim_ids
        if no claim_ids: DROP the sentence
    if section is now empty: omit the section
```

Sentence splitting uses a proper segmenter (`pysbd` or equivalent), not a regex on periods — `$5.00/mo` and `e.g.` both break naive splitting, and a mis-split sentence gets wrongly dropped or wrongly bound.

The generation prompt requires per-sentence citation markers so binding is deterministic rather than inferred. A sentence whose marker references a nonexistent finding is dropped.

**Verified programmatically at assembly**: every string field in the report that contains prose is checked to have non-empty `claim_ids` reachable from it. This is an assertion in `assemble.py`, not a test — a report that fails it is not returned.

### Report assembly

Constructs the masterplan §2 contract exactly. The [Phase 00](phase-00-foundation-contracts-ci.md) `Report` model is the target, and the §2 example JSON is already a parsing fixture.

Two computed sections deserve note:

**`freshness`** — `median_source_age_days` and `oldest`, computed from claim `as_of` where present, else source `fetched_at`. This is what makes "stale claims read as current" (masterplan §13) visible rather than latent.

**`coverage`** — from [Phase 08](phase-08-grading-confidence-contradictions.md), reported separately from confidence per Rule 4. A run whose funding branch died says so on the report.

`meta` carries `cost_usd`, `duration_s`, `sources_fetched`, `cache_hit_rate` — the transparency that makes the cost story checkable rather than claimed.

---

## Testing

| Kind | Test | Asserts |
|---|---|---|
| Unit | Sentence splitting handles `$5.00/mo`, `e.g.`, `Inc.`, abbreviations, ellipses | No mis-splits |
| Unit | Sentence with no citation → dropped | Rule 1 |
| Unit | Section emptied by drops → omitted, not empty-string | Clean output |
| Unit | Synthesis validation rejects <3 findings referenced | §4.9 |
| Unit | Synthesis validation rejects zero complaint-derived findings | §4.9, the sharper condition |
| Unit | Rejection twice → section omitted + coverage gap recorded | Honest failure |
| Unit | Finding statement templates render with correct real N | No invented numbers |
| Unit | Freshness arithmetic: median and oldest, `as_of` preferred over `fetched_at` | Correct staleness |
| Integration | Clustering merges near-duplicate themes; support summed post-cluster | Promotion sees clustered totals |
| Integration | Clustering does **not** merge genuinely distinct complaints | Over-merge guard |
| Integration | Promotion applied after clustering, not before | Ordering matters |
| Integration | Full assembly from seeded claims → valid `Report` | Contract satisfied |
| Integration | **100% sentence binding** — every prose sentence in a generated report resolves to ≥1 claim_id, which resolves to a real span in a real source | The product promise, verified mechanically |
| Integration | Synthesis prompt receives **no page text** — asserted by inspecting the rendered prompt | Injection boundary + anti-generic guard |
| Integration | Contradictions from [Phase 08](phase-08-grading-confidence-contradictions.md) appear in the report with both values, grades, dates | Losers surfaced |
| Integration | A run with a dead branch produces a report naming the failed branch | Rule 4 |
| Integration | Report parses back into the `Report` model; round-trips to JSON unchanged | Contract stability |
| Integration | **Generic-advice regression** — a run whose findings contain no complaints produces no MVP section | The guard fires |

The 100% sentence binding test is the phase's signature and should be written to be readable, because it *is* the product claim in executable form: walk every prose sentence, follow its claim_ids to claims, follow those to sources, and assert the quote appears at the recorded span. If that test passes, the interview sentence from masterplan §1 is literally true.

---

## Exit criteria

- [ ] Findings generated per kind, with promotion thresholds applied after clustering
- [ ] Complaint clustering implemented via pgvector; over-merge guard tested
- [ ] Real N stored and rendered; no invented counts anywhere
- [ ] Synthesis receives findings only — proven by prompt inspection
- [ ] Validation rejects <3 findings and zero-complaint output; both tested
- [ ] Rejected sections omitted and recorded as coverage gaps, never emitted generic
- [ ] Sentence-level binding drops unbindable sentences
- [ ] **100% sentence binding verified end to end**, span-checked against source text
- [ ] `Report` matches masterplan §2 contract; round-trips
- [ ] `freshness`, `coverage`, `contradictions`, `meta` all populated correctly
- [ ] Assembly asserts binding and refuses to return a violating report
- [ ] Full suite offline
- [ ] Coverage ≥ 85% on `src/api/synth/`

---

## Risks

| Risk | Mitigation |
|---|---|
| Sentence dropping leaves incoherent prose | Whole sections are omitted when emptied, rather than leaving fragments. Prompt asks for self-contained sentences so a drop does not break neighbours. Reviewed against real output in [Phase 14](phase-14-benchmark-calibration.md). |
| Synthesis constantly rejected, reports have no MVP | Rejection rate is a tracked metric. If high, the fix is prompt tuning — never loosening the three-finding or complaint requirement, which are the guard itself. |
| Clustering over-merges distinct complaints | Conservative starting threshold; explicit over-merge test; calibrated against hand-labelled data in [Phase 14](phase-14-benchmark-calibration.md). |
| Sentence splitter mis-handles pricing text | Proper segmenter, not regex; explicit test cases for the currency and abbreviation cases that actually appear in this domain. |
| A generated sentence cites a real finding but misrepresents it | Beyond mechanical binding — binding proves provenance, not faithfulness. Partially addressed by giving synthesis only finding statements (no room to invent detail). Fully addressed only by [Phase 14](phase-14-benchmark-calibration.md) fact-accuracy measurement against ground truth. **Worth stating plainly rather than overclaiming.** |
| Report grows large enough to be slow to store | `reports.payload` is JSONB; measured in [Phase 14](phase-14-benchmark-calibration.md) against the Supabase 500 MB ceiling alongside source text. |

## Open decisions

1. **Should low-confidence findings be shown at all?** They carry citations, so Rule 1 is satisfied, but a wall of 0.35-confidence D-grade findings is noise. Proposal: include with visual de-emphasis in [Phase 13](phase-13-frontend.md) rather than filtering server-side — the drill-down is the point, and hiding evidence undercuts it.
2. **Are `feature_gap` findings sound?** A gap is inferred from *absence* of a `feature.<slug>.present` claim, and absence of evidence is weak evidence of absence — a competitor may have the feature undocumented on the pages fetched. Proposal: phrase gaps as "not found in reviewed sources" rather than "does not exist", and cite the pages actually checked. Decide before [Phase 13](phase-13-frontend.md) fixes the wording in UI.
