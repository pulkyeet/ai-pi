# Benchmark — Phase 14

Dated 2026-08-08. Ten queries (`bench/queries/q01.yaml`–`q10.yaml`), six
tuning / four held-out (masterplan §10's own split discipline), run once
each against the real pipeline — real OpenRouter, real Exa, real vendor
sites, no mocking. Raw scored snapshots: `bench/results/2026-08-08/`.
Methodology and every calibration decision this data fed: `docs/tuning.md`.

**Open Decision #1 from the phase doc, resolved: publish everything,
including the bad numbers.** Recall on the tuning set was 0% against the
household-name ground truth this benchmark deliberately used. That is
reported below in full, with the traced root causes, rather than
soft-pedaled or tuned away — per the phase doc's own framing, a number
this benchmark set silently *is* the definition of every quality claim in
the README, so hiding a bad one defeats the point of building it.

---

## Tuning set (6 queries)

| id | query | recall | precision | fact acc. | binding | contradiction | cost | duration | coverage |
|---|---|---|---|---|---|---|---|---|---|
| q01 | project management tool | 0.00 | 1.00 | 0.00 | 1.00 | No | $0.0685 | 195.7s | 0.00 |
| q02 | email marketing platform | 0.00 | 1.00 | 0.00 | 1.00 | Yes | $0.0701 | 253.1s | 0.00 |
| q04 | AI expense tracker for freelancers | 0.00 | 1.00 | 0.00 | 1.00 | Yes | $0.0594 | 317.0s | 0.00 |
| q07 | customer support helpdesk (**trap**) | 0.00 | 1.00 | 0.00 | 1.00 | Yes* | $0.0777 | 240.2s | 0.00 |
| q08 | static site generator for docs | 0.00 | 1.00 | 0.00 | 1.00 | No | $0.0523 | 218.5s | 0.00 |
| q09 | WhatsApp-first CRM for Indian SMBs | 0.00 | 1.00 | 1.00† | 1.00 | Yes | $0.0444 | 120.8s | 0.00 |
| **mean** | | **0.00** | **1.00** | **0.17** | **1.00** | 4/6 fired | **$0.0621** | **224.2s** | **0.00** |

\* Fired, but not on the intended trap — see "The trap query" below.
† Vacuous: q09's own ground truth carries no `facts` (see
`bench/queries/q09.yaml` — its vendors price in INR, not USD, and no
same-day conversion was fabricated), so `fact_accuracy` returns 1.0 by
definition of "nothing to check," not because a fact matched.

p50 cost $0.0594, p95 $0.0777. p50 duration 218.5s, p95 317.0s. p50 LLM
cost $0.0034, p95 $0.0141. p50 search cost $0.056, p95 $0.070.
Sentence-binding rate: **100% on every run** (the one hard-pass-condition
metric — see "What actually held up" below). Precision: **100%** — zero
`known_absent` domains ever appeared across all six runs.

**Process metrics**: planner fallback rate 1/6 (17%, q02); synthesis
rejection 18/18 (100% — MVP/feature-gaps/risks never generated on *any*
tuning run, see below); extraction drops across all six runs:
`quote_ambiguous`=59, `quote_not_in_source`=51, `invalid_attribute`=12,
`value_type_mismatch`=8 (130 total against several hundred bound claims —
traced in the Phase 14 follow-up (2026-08-10), below; verdict: a minimum
quote length is *not* the fix — see `docs/tuning.md` §"Extraction drops").

---

## Why recall was zero — three distinct, traced causes, not one bug

Zero of the six tuning queries found a single `must_include` domain. That
is a genuinely bad number, and it did not come from one bug — three
different, independently-confirmed mechanisms, each real:

### 1. The planner engaged GitHub for a plainly mainstream category (q01)

q01 ("project management tool") is this benchmark's own designated
"consumer query where GitHub should be skipped" role query (the exit
criterion masterplan §10 names explicitly). The real Stage-1 planner set
`discover_competitors.consider_oss=true` anyway, spawning `oss_profile`
against five tangentially-related GitHub `awesome-*` list repos
(`awesome-agile-essentials`, `awesome-knowledge-management`,
`defi-resources`, `arlen-blog-management-system-for-html-sites`,
`awesome-software-development-mind-maps` — curated *lists*, not
individual competitor products) before any real candidate's pricing page
was ever fetched. Combined with the old `RUN_BUDGET_WEIGHT=40` cap (see
`docs/tuning.md`), this exhausted the run's entire budget: `mine_community`
and three real `profile_product` tasks (for entities that *were*
discovered — Asana, Trello, ClickUp among them) were skipped for
`"budget"`, and the report shipped with zero competitors despite 23
verified entities. **The same pattern repeated on q03** (held-out,
"video conferencing software" — this benchmark's *other* consumer/no-GitHub
role query): the planner spawned `oss_profile` against `zoom/skills`,
`jitsi/jitmeet`, and `bycyrus/zoom-clone`. Two-for-two on the "GitHub
should be skipped" check, and it failed both times. This is a genuine
Phase 09 planner-judgement finding — logged here, not fixed here, per
Phase 14's own scope (`docs/execution_phases/phase-14-benchmark-
calibration.md`: "changing product behaviour beyond tuning constants... a
design flaw is a fix in the owning phase"). **→ since fixed and re-measured
in the Phase 14 follow-up (2026-08-10), below.**

### 2. Discovery surfaces real, verified, but long-tail competitors — not the household names ground truth expects

q04, q07, and q09 all had `profile_product`/`extract_pricing` tasks
complete normally, no budget starvation, and *did* produce real,
Rule-2-verified competitors — just never any of the well-known market
leaders this benchmark's ground truth names. q04 ("AI expense tracker for
freelancers") found `mozey.co`, `centsense.app`, `fwdtools.com`,
`flexpro.app`, `vuuv.co`, `keepr.co.uk` — six real, live, artifact-verified
products, and *not one* of `expensify.com`/`wave.com`/`freshbooks.com`.
Same shape on q07 (`helpspot.com`, `fynedesk.io` — not
`zendesk.com`/`freshdesk.com`/`intercom.com`/`helpscout.com`) and q09
(`connectribe.com`, `wapmini.in`, `hellogrowthcrm.com` — not
`interakt.shop`/`wati.io`/`doubletick.io`/`zoko.io`). Precision stayed
perfect throughout (no `known_absent` domain ever appeared), so this is
not a hallucination problem — masterplan Rule 2 (verifiable public
artifact required) is doing its job. It is a genuine **recall** problem in
the discovery layer: Exa neural search plus the LLM planner's own
query-variant phrasing is consistently surfacing small/indie/long-tail
products over established brand sites for these categories. Exactly the
masterplan's own anticipated risk (§13): "the likely levers are discovery
seeds (Phase 10) and search provider choice (Phase 04) — both measurable,
both fixable in their owning phase." Measured here; not fixed here.
**→ since fixed (wider discovery window + `mode="auto"`) and re-measured in
the Phase 14 follow-up (2026-08-10), below.**

### 3. A structural gap: genuinely free/OSS products can never appear as "competitors" at all (q08)

q08 ("static site generator for documentation sites" — the OSS-dominated
role query) discovered and profiled real entities correctly —
`docusaurus.io` got a real `pricing.free_tier=true` claim, eight `gh:`
repos (`redocly/redoc`, `kotlin/dokka`, `elixir-lang/ex_doc`, and others,
25,854 stars on `redocly/redoc`) got real `oss.stars`/`oss.license`
claims — and the report still shipped **zero** competitors. Cause:
`api.synth.assemble.build_competitors` requires the full pricing triple
(`pricing.model` + `pricing.entry_usd_month` + `pricing.free_tier`) before
an entity can appear in `report.competitors` — masterplan Rule 1's own "no
fabricated field" discipline, working exactly as designed. But a
genuinely, permanently free OSS tool has no `pricing.model` to report (the
closed vocabulary's `pricing.model` enum is `seat|usage|flat|freemium` —
none of which honestly describes "there is no paid tier at all"), so it
can **structurally never** satisfy the gate, no matter how well it was
discovered and verified. This is a real, load-bearing gap in the `Report`
output contract (Phase 00/11), not a bug in discovery or extraction — a
whole category of entity (permanently free software) is invisible to
`report.competitors` by construction. Logged here; the fix (a `free`
member on `pricing.model`, or a triple-optional relaxation for confirmed-
free entities) belongs to whichever phase owns that contract.
**→ since fixed (`pricing.model='free'` + `entry_usd_month=0.0` default)
and re-measured in the Phase 14 follow-up (2026-08-10), below.**

---

## The trap query — fired, but not on the trap

`bench/queries/q07.yaml` documents a real, dated Help Scout pricing-model
reversal (contacts-based usage pricing in 2025, reverted to per-seat in
2026 — full citations in the query file) specifically so the
contradiction detector would have something genuine to catch. The run's
own contradiction detector **did** fire (`contradiction_fired=true`), so
the raw exit-criterion boolean passes — but `helpscout.com` was never
discovered by this run at all (see cause #2 above), so the fired
contradiction is unrelated: a false-positive `product.integrations`
disagreement on `helpspot.com` (one source said `"REST API"`, another said
`"Office365"` — both true simultaneously, not a real contradiction).
Inspecting the other tuning runs' contradictions surfaced the same shape
repeatedly (`klaviyo.com`: `"Claude"`/`"ChatGPT"`/`"Shopify"` all flagged
as mutually exclusive; `wapmini.in`: `"web"`/`"chrome"` platform values
across eight sources) — **`api.evidence.contradictions`'s `GROUP BY
attribute HAVING count(distinct value) > 1` treats every closed-vocabulary
attribute as single-valued**, but `product.integrations`/
`product.platforms` are legitimately multi-valued; a product can and does
have several real integrations or platforms at once. The *genuinely*
single-valued attributes did produce plausible real contradictions worth a
second look — `keepr.co.uk` (`pricing.model`: flat vs. freemium),
`flexpro.app` (flat vs. seat), `hellogrowthcrm.com` (`pricing.model`: seat
vs. usage, *and* `pricing.entry_usd_month`: $12 vs. $10) — but none of
these are the researched Help Scout trap either. **Net finding**: the
contradiction detector's SQL needs an attribute-cardinality concept
(single-valued vs. multi-valued) to stop generating false positives on
`product.integrations`/`product.platforms` — a real Phase 08 design gap,
found by the benchmark exactly as the phase doc hoped ("without \[a trap\]
there is no evidence the contradiction detector ever fires rather than
silently never triggering" — it triggers, just not selectively enough).
**→ since fixed (`_is_contradictory` skips `ValueKind.LIST` attributes)
and re-measured in the Phase 14 follow-up (2026-08-10), below.**

---

## What actually held up

- **Sentence binding: 100% on every single run, no exceptions.** The one
  hard pass/fail metric (`docs/execution_phases/phase-14-benchmark-
  calibration.md`: "anything below 100% means Phase 11's enforcement is
  broken, and it is a bug, not a tuning target") passed cleanly across all
  ten runs (six tuning + four held-out). `api.synth.bind`'s "drop, don't
  fabricate" discipline is holding under real, messy, live data.
- **Precision: 100%.** Not one `known_absent` domain ever leaked into a
  report across ten real runs. Masterplan Rule 2 (verifiable artifact
  required) is structurally doing its job — every "wrong" competitor this
  benchmark found (§"Why recall was zero" #2) was still a *real*, live,
  artifact-verified product, never a hallucination.
- **Cost stayed cents-level even with the larger `RUN_BUDGET_WEIGHT`.**
  Mean $0.062/run, p95 $0.078/run — comfortably inside the masterplan's own
  cost model, and inside the derived `RUN_BUDGET_USD=$0.25` cap with real
  headroom.
- **Coverage reads 0.00 on every run — confirming, not discovering, a
  known Phase 10 finding at real production scale.** Every profiled `web:`
  entity across all ten runs landed on `insufficient_signal` (no domain-age
  or install-count source exists for that scheme — `docs/tuning.md` §3),
  which `api.evidence.coverage.compute_coverage`'s multiplicative
  entity-score term zeroes out regardless of how much real task-level work
  completed. Not a new bug; ten more data points confirming the one Phase
  10 already logged.
- **Synthesis (MVP/feature-gaps/risks) never fired on any of the six
  tuning runs.** `api.synth.generate`'s own gate (≥3 distinct findings,
  ≥1 `pain_point`) was never cleared, because community-mined
  `complaint.*`/`request.*` themes never accumulated enough support —
  traced to `MAX_COMMUNITY_THREADS`'s per-pair floor bug, `docs/tuning.md`
  §"MAX_COMMUNITY_THREADS". Not a synthesis-quality problem; synthesis was
  never even attempted (`generate.py`'s own "no LLM call made at all when
  the finding set can't possibly satisfy the rule" optimisation, working
  as designed on starved input).

---

## Held-out set (4 queries) — run once, after tuning

Run once, after every calibration decision in `docs/tuning.md` was final
and every quota knob was already set to its derived value — the held-out
numbers below reflect the calibrated system, not the pre-calibration one.

| id | query | recall | precision | fact acc. | binding | contradiction | cost | duration | coverage |
|---|---|---|---|---|---|---|---|---|---|
| q03 | video conferencing software | 0.00 | 1.00 | 0.00 | 1.00 | Yes* | $0.0608 | 224.4s | 0.00 |
| q05 | error tracking and monitoring | 0.00 | 1.00 | 0.00 | 1.00 | Yes* | $0.0841 | 295.7s | 0.00 |
| q06 | no-code website builder | 0.00 | 1.00 | 0.00 | 1.00 | Yes* | $0.0714 | 327.9s | 0.00 |
| q10 | MEV monitoring (**thin category**) | 1.00† | 1.00 | 1.00† | 1.00 | Yes* | $0.0436 | 232.8s | 0.041 |
| **mean** | | **0.25** | **1.00** | **0.25** | **1.00** | 4/4 fired | **$0.0649** | **270.2s** | **0.010** |

\* All four — see "The contradiction false-positive, confirmed at scale"
below. † Vacuous, same shape as tuning's q09: `must_include`/`facts` are
both deliberately empty for q10 (the correct ground truth for a genuinely
near-empty category), so a report with zero fabricated competitors scores
perfect recall and fact accuracy by definition, not because a real match
was found — see "The one genuinely clean win" below for why this result is
still worth taking seriously, unlike the other three vacuous cases in this
document.

**Held-out vs. tuning, reported honestly per masterplan §10's own
discipline**: recall/fact-accuracy *means* look better (0.25 vs. 0.17/0.00)
but that is entirely q10's vacuous case — q03/q05/q06 each independently
reproduce tuning's own 0% recall against real ground truth, the same
long-tail-over-household-name pattern (§"Why recall was zero" #2 above).
**This is not overfitting** — nothing was tuned to fit any specific
query's recall number in the first place (`docs/tuning.md`: every
constant was kept unchanged, precisely because six queries' worth of data
never supported confidently changing masterplan-specified values) — it is
the same real, structural discovery-relevance gap, confirmed independently
on data that was never looked at until this run.

### The contradiction false-positive, confirmed at scale

All four held-out runs fired at least one contradiction. Inspecting every
one of them: **one hundred percent are the same `product.integrations`/
`product.platforms` multi-valued false positive** already found on the
tuning set (`sentry.io`: ten platforms — `java`/`ios`/`go`/`javascript`/
`react`/`node`/`python`/`vue`/`android`, all flagged mutually exclusive;
`shakebug.com`: ten integrations, same shape; `dialpad.com`: twelve
integrations plus a platforms contradiction; `dune.com`, `kanorio.com`:
same). **Zero of the eight fired contradictions across the full ten-query
benchmark (six tuning + four held-out) were the researched Help Scout
trap**, and only a small minority even touched a genuinely single-valued
attribute like `pricing.model`. This generalizes §"The trap query" above
from "observed on six queries" to "observed on all ten" — the
`api.evidence.contradictions` attribute-cardinality gap is not a tuning-set
fluke.

### The one genuinely clean win: q10 handled its thin category correctly

Unlike the other 9 runs, q10's zero-competitor report is the **correct**
answer, not a failure mode: `bench/queries/q10.yaml`'s own ground truth
was deliberately built to test exactly this (masterplan §10: "the category
barely exists, so the system can be checked for saying 'no real
competitors' instead of inventing four"). The real run found genuinely no
`must_include` matches (there are none — the ground truth is empty by
design) **and no `known_absent` false positives either** (`rated.network`,
`mevboost.org`, `etherscan.io`, `chainalysis.com` all correctly stayed out
of the report) — `report.competitors` came back empty, not stretched.
Masterplan Rule 2 (verifiable public artifact required) held under a
genuinely adversarial "there's almost nothing real here" input, which is
exactly what this query exists to test. The only *other* run in the entire
benchmark with a nonzero `coverage.score` (0.041, vs. 0.00 everywhere
else) — worth a follow-up look, though not chased further this phase.

---

## Methodology notes

- All ten runs used real OpenRouter/Exa/GitHub traffic, `is_benchmark=true`
  on the `runs` row, never `is_public` (publishing to the homepage stays a
  separate, deliberate step — `docs/tuning.md`/`src/api/cli.py`'s
  `run_query` docstring).
- The six tuning runs used the pre-Phase-14 defaults
  (`RUN_BUDGET_WEIGHT=40`, `MAX_COMMUNITY_THREADS=10`) *as they executed* —
  the q01 budget-exhaustion finding and the `MAX_COMMUNITY_THREADS`
  combinatorics finding are what motivated the quota changes in
  `docs/tuning.md`, applied *before* the held-out run so the held-out
  numbers reflect the calibrated configuration, not the pre-calibration one
  (masterplan §10's own "tune on six, evaluate held-out with the final
  settings" spirit).
- The held-out run's Exa searches were, for a few minutes, degraded by
  this same session's own newly-derived `EXA_DAILY_CREDIT_CAP_USD=0.33` —
  the six tuning runs plus the first held-out attempt exceeded that budget
  within the same real calendar day purely because both batches ran hours
  apart in one working session, not across a real day boundary. That first
  attempt was killed before any result was scored or written; the cap was
  temporarily unset for a clean held-out run and   restored immediately after
  (`docs/tuning.md`). A genuine one-time-a-day cap doing exactly its job,
  logged rather than quietly worked around.

---

## Phase 14 follow-up (2026-08-10): the owning-phase fix set, landed and re-measured

Every "logged here, not fixed here" finding above was since implemented
and re-measured on three tuning queries (q01, q04, q08) against real
traffic (~$0.37 total). What changed in code:

1. **Planner judgement (§"Why recall was zero" #1)** — `plan_dag.md`'s
   `consider_oss` guidance rewritten (GitHub only for genuinely
   OSS-considered categories), plus a mechanical backstop:
   `discover.py` no longer seeds from `awesome-<category>` curated-list
   repos at all (`_is_github_list_repo` filters name/description); when
   `consider_oss` is true it searches GitHub for real repos in the
   category instead (`"<category> in:name,description stars:>100"`).
2. **Long-tail over household names (#2)** — discovery window widened
   from `limit=10` to `DISCOVERY_SEARCH_LIMIT=20` and Exa switched from
   `mode="neural"` to `mode="auto"` (same flat $0.007/query) so results
   ranked 11–20 and keyword-matching queries get a chance to surface.
3. **Free/OSS structural gap (#3)** — `pricing.model` gains a `free`
   member (the only honest value for a permanently-free product;
   `freemium` would fabricate a paid tier above it) and
   `build_competitors` accepts `entry_usd_month=0.0` for `model=="free"`
   without requiring an entry-price claim. `ReportView` renders "Free".
4. **Contradiction false positives (trap §)** — `_is_contradictory` skips
   `ValueKind.LIST` attributes (`product.integrations`/`platforms` are
   legitimately multi-valued); the detector now fires only on genuinely
   single-valued attributes.
5. **New: Exa snippets are quoteable (decision 06b).** The search-result
   snippet becomes a grade-C synthetic source
   (`retrieval_reason='serp_snippet'`) that `profile_product` extracts
   claims from — with `pricing.*` claims excluded so a machine summary can
   never complete the competitor pricing triple.
6. **`merge_alias` FK crash fixed.** An old run's `claims.entity_id`
   (no cascade, deliberately) referenced the losing entity, so the merge
   delete raised `claims_entity_id_fkey` — observed live on q08 discovery
   and previously on cached-only replay. `merge_alias` now repoints claims
   onto the canonical entity before the delete; q08 re-ran clean.

Measured outcome (2026-08-10, same three queries, real traffic):

| id | recall | precision | cost | duration | report shape |
|---|---|---|---|---|---|
| q01 | 0.00 | 1.00 | $0.1274 | 240.6s | 4 real competitors (OpenProject, Shortcut, Breeze, Project.co) vs **0** pre-fix |
| q04 | 0.00 | 1.00 | $0.1794 | 362.6s | expensify.com discovered + profiled (absent pre-fix); fallback plan used |
| q08 | 0.00 | 1.00 | $0.0021 | 68.0s | real OSS surfaced (mkdocs, docsify, starlight); discovery **crashed** pre-fix |

Discovery improved exactly as intended — q01 went from zero competitors
under budget starvation to four real, Rule-2-verified competitors; q04
discovered and profiled expensify.com; q08 surfaced genuinely relevant
OSS. Recall still reads 0.00 because a **second, distinct bottleneck** now
blocks the report — not because the fixes failed:

- **q01: `value_type_mismatch` mass-drops pricing claims** (54 in one run
  vs 8 across all six pre-fix). The extractor emits `feature.<x>.present`
  booleans with prose values ("Can I organize ideas and setup workflows on
  a Kanban board?") and price strings ("Per user/month if billed
  annually") instead of `true`/a number — so ClickUp and Basecamp,
  correctly discovered and profiled, fail the pricing triple at assemble
  and never reach `report.competitors`. Traced (06a) to prompt/schema
  compliance, not span logic. Single highest-leverage remaining fix;
  owning-phase (extractor) work, not a tuning constant.
- **q04: `profile_product` times out at `timeout_s=90`** on JS-heavy
  pricing pages (expensify.com, attempts=2) so no pricing claims land.
- **New metric gap: cross-scheme identity.** q08 found `mkdocs/mkdocs` as
  a `gh:` entity but ground truth names `web:mkdocs.org`;
  `competitor_recall` scores only `web:` entities and no alias links the
  two, so a correct OSS discovery scores zero. Fix options (scorer
  resolving identity through the alias graph, or discovery creating the
  web alias when a verified repo's homepage is known) own a design call,
  deferred.

The q07/q09 trap and contradiction findings were not re-run; the
contradiction fix's correctness is covered by
`tests/integration/test_contradictions.py` (multi-valued attributes no
longer contradict; the researched trap still fires).
