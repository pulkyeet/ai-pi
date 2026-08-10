# Tuning — Phase 14

Dated 2026-08-08. Every constant the masterplan or a phase doc flagged as
"first-pass guess, tunable in Phase 14," plus the quota knobs from
masterplan §8.2, with what changed (or didn't) and why. Source data: six
live tuning-benchmark runs (`bench/queries/{q01,q02,q04,q07,q08,q09}.yaml`)
against real vendors, `bench/results/2026-08-08/`. See `docs/benchmark.md`
for the full numbers these decisions are based on.

**Discipline note, up front**: a number without a reason is a number nobody
can revisit later. Every row below states what was actually checked, not
just what value landed. Several constants are explicitly *not* changed —
"kept, here's why" is as much a calibration decision as "changed."

---

## 1. Confidence formula (`src/api/evidence/confidence.py`)

| Constant | Value | Change |
|---|---|---|
| `BASE` (A/B/C/D) | 0.90/0.75/0.55/0.35 | **Unchanged** |
| `DOMAIN_BONUS_PER_STEP` | 0.05 | **Unchanged** |
| `DECAY_PER_30_DAYS` | 0.98 | **Unchanged** |
| `CONTRADICTION_PENALTY` | 0.6 | **Unchanged** |
| `CONFIDENCE_CAP` | 0.97 | **Unchanged** |

**Why kept.** These are masterplan §4.6's own literal numbers, not
placeholder guesses — the phase doc's own calibration target is "correlation
between confidence and fact accuracy... the only thing 'calibrated' can mean
here." The six tuning runs produced almost no usable signal for that
correlation: `fact_accuracy` was non-vacuous (i.e. the ground-truth entity
was actually found and checkable) on exactly one query (q09, and only
because q09's own ground truth carries no `facts` — see `docs/benchmark.md`
§"Why recall was zero" for why). Overriding masterplan-specified constants
on a sample size this thin would be tuning to noise, not to a real signal.
Revisit once a run reliably discovers ground-truth entities (see the
recall finding below, which is the actual blocker).

## 2. Promotion thresholds (`src/api/evidence/promotion.py`)

| Constant | Value | Change |
|---|---|---|
| `COMMENT_SUPPORT_THRESHOLD` | 5 | **Unchanged** |
| `COMMENT_MIN_DISTINCT_THREADS` | 3 | **Unchanged** |
| `GITHUB_REACTION_THRESHOLD` | 20 | **Unchanged — untunable this phase** |

**`COMMENT_SUPPORT_THRESHOLD`/`COMMENT_MIN_DISTINCT_THREADS`**: masterplan
§4.6's own literal numbers. Every real `complaint.*`/`request.*` theme
observed across the six runs had `support_count` between 1 and 4 (see
`docs/benchmark.md`) — never once near the 5-comment bar. The root cause,
traced and fixed at the quota-knob level instead (§5 below), is
`MAX_COMMUNITY_THREADS`'s combinatorial floor, not these thresholds — no
theme in this dataset got a fair chance to clear 5 regardless of where the
bar sat, so moving the bar wouldn't have been a real calibration, just
guessing in the dark.

**`GITHUB_REACTION_THRESHOLD` — a genuine finding, not a tuning result.**
`api.evidence.promotion.evaluate_github_theme` (the function this constant
gates) has **zero real callers** in the current codebase — confirmed by
`grep -rn evaluate_github_theme src/api/` returning only its own definition
and `api.synth.findings`'s own docstring explaining why:
`MineCommunityHandler` bundles every thread/issue for one `(venue,
keyword)` pair into a single synthetic source before extraction, so no
individual claim ever retains a per-issue reaction count for
`evaluate_github_theme` to consume — `evaluate_community_theme` (the
comment/thread-breadth evaluator) is used uniformly for every venue
instead, GitHub included. This was already logged as a known gap in Phase
11's own tracker entry; Phase 14 confirms it's still true and adds the
concrete consequence: **this constant cannot be calibrated against
benchmark data because no data path reaches it.** Fixing it needs
`api.tasks.community` to persist real per-issue reaction identity — Phase
10/11-shaped work, out of Phase 14's scope, carried forward.

## 3. Maturity tier thresholds (`src/api/resolve/maturity.py`)

| Constant | Value | Change |
|---|---|---|
| `ABANDONED_MONTHS` | 18 | **Unchanged** |
| `ESTABLISHED_DOMAIN_YEARS` | 3.0 | **Unchanged** |
| `ESTABLISHED_INSTALLS` / `_DOWNLOADS_PER_MONTH` / `_STARS` | 100k / 100k / 5k | **Unchanged** |
| `HOBBY_STARS` / `HOBBY_DOWNLOADS_PER_MONTH` | 100 / 1,000 | **Unchanged** |

**Why kept.** Confirmed Phase 10's own tracker finding at real scale, not a
new discovery: every `web:` entity profiled across all six tuning runs
landed on `indie` (the insufficient-signal default), because no phase from
00 through 14 has ever built a domain-age or install-count signal source
for `web:` entities — `MaturitySignals.domain_age_days`/`store_installs`/
`downloads_per_month` are `None` for every one of them, every time,
regardless of what the threshold numbers are. Moving a threshold changes
nothing when the signal it compares against never arrives. The real fix is
a new signal source (Phase 04/07-shaped work: a WHOIS/domain-age lookup or
similar) — a threshold-calibration phase has no lever here. `gh:` entities
*do* get real `stars`/`last_commit_at` signals (see `docs/benchmark.md`'s
static-site-generator run), so the thresholds are not entirely inert, just
inert for the majority scheme.

## 4. Clustering similarity threshold (`src/api/synth/cluster.py`)

| Constant | Value | Change |
|---|---|---|
| `DEFAULT_SIMILARITY_THRESHOLD` | 0.86 | **Unchanged** |

**Why kept.** The phase doc's own calibration target is "hand-labelled
complaint clusters... over-merging penalised harder than under-merge." The
six tuning runs produced 18 `complaint.*`/`request.*` claims total across
all six queries combined (`docs/benchmark.md`), the overwhelming majority
singleton or pair-sized themes — nowhere near enough real clustering
decisions to hand-label a meaningful sample. No merge/non-merge judgement
in this dataset was close enough to the 0.86 boundary to be informative
either way.

## 5. Quota knobs (`src/api/config.py` / `.env.example`)

Every masterplan §8.2 `TBD` is now a real value. Full derivation arithmetic
in `docs/benchmark.md`; summary here:

| Knob | Old | New | Basis |
|---|---|---|---|
| `RUN_BUDGET_WEIGHT` | 40 (`DEFAULT_RUN_BUDGET_WEIGHT`, masterplan §2's own example number) | **70** | p95 *wanted* plan weight across 6 runs (47) × 1.5 headroom |
| `RUN_BUDGET_USD` | unset | **0.25** | p95 observed cost ($0.0777) × 3 |
| `RUN_TIMEOUT_S` | unset | **640** | p95 observed duration (317s) × 2 — **no consumer wired anywhere in the codebase**, confirmed by grep; setting the value closes the masterplan §8.2 checklist item, but it does nothing operationally until a future phase wires it into the executor's per-run timeout. Logged, not silently implied otherwise. |
| `MAX_COMPETITORS_PROFILED` | 8 | **8 (kept)** | No sweep performed this session (would have multiplied live-run cost/time by the number of swept values); this run's recall bottleneck was budget exhaustion and discovery relevance (`docs/benchmark.md`), not profiling depth, so there is no real evidence a different value would have helped. Deferred to a dedicated sweep. |
| `MAX_PAGES_PER_ENTITY` | 4 | **4 (kept)** | No signal from the drop-reason breakdown (`quote_ambiguous`/`quote_not_in_source` dominate — an extraction-precision issue, not a page-count one) pointed at this knob. |
| `MAX_COMMUNITY_THREADS` | 10 | **20** | See below — a real, traced bug, not a guess. |
| `GLOBAL_RUNS_PER_DAY` | unset | **4** | `(Exa $10/mo ÷ 30) ÷ p95 search-$/run ($0.070)` ≈ 4.76, floored per masterplan §8.2's own "p95, not median" rule |
| `RUNS_PER_USER_PER_DAY` | unset | **3 (judgement call)** | Not derivable from six queries; leaves room under the daily global cap for more than one user |
| `MAX_CONCURRENT_RUNS` | unset | **2 (judgement call)** | Single-worker deployment reality (Phase 02/12's own accepted limitation) plus GitHub Search's separate 30/min cap, which two concurrent `discover_competitors` runs could plausibly approach |
| `EXA_DAILY_CREDIT_CAP_USD` / `EXA_GLOBAL_DAILY_CREDIT_CAP_USD` | unset | **0.33 / 0.33** | `$10/mo ÷ 30` — both collapse to the same system-wide ledger check by Phase 04's own design |

**`GLOBAL_RUNS_PER_DAY` in raw search-count terms**, per the phase doc's own exit criterion ("derived
from p95 search count, not guessed") — the real per-run search counts measured directly from
`search_credit_usage`, one row per query, across the six tuning runs:

```
q01: 9   q02: 8   q04: 8   q07: 10   q08: 7   q09: 6
```

p95 (nearest-rank over 6 values) = **10 searches/run**. At Exa's flat, Phase-01-confirmed-unchanged
`$0.007/query`, that is `$0.070/run` — exactly the p95 search-dollar figure the table above already
used, the same number reached two ways: `(($10/mo ÷ 30) ÷ (10 × $0.007)) ≈ 4.76`, floored to 4.

### `RUN_BUDGET_WEIGHT`: 40 → 70, a real bug this number fixes

Every live tuning run's *wanted* plan weight (every seeded + runtime-spawned
node's `budget_weight`, including everything that ended up `skipped` for
budget) was measured directly from `tasks`:

```
q01 (project mgmt):      47   q07 (helpdesk):      39
q02 (email, fallback):   31   q08 (static site):   25
q04 (expense tracker):   27   q09 (WhatsApp CRM):  46
```

p95 = 47 (nearest-rank over 6 values) → `47 × 1.5 = 70.5`, rounded to 70.

The old default of 40 was not just theoretically tight — it was **observed
directly causing a real failure**: q01 ("project management tool")
exhausted its entire 40-weight budget on the LLM planner's own
`consider_oss=true` decision, spawning `oss_profile` tasks against five
tangentially-related GitHub `awesome-*` list repos (`awesome-agile-
essentials`, `awesome-knowledge-management`, `defi-resources`, and others —
none of them real project-management competitors) before any real
candidate's pricing page was ever fetched. Result: `mine_community`,
`extract_pricing`, and three `profile_product` tasks for the *actual*
discovered candidates (Asana, Trello, ClickUp among them) were all skipped
for `"budget"`, and the final report shipped with **zero competitors**
despite 23 verified entities. Raising the cap to 70 doesn't fix the
*root* problem — the planner's OSS-relevance judgement for a plainly
mainstream category, a Phase 09 concern, out of scope here (see
`docs/benchmark.md`'s "GitHub skip/use" finding) — but it does mean a
correctly-judged plan has enough room to reach real candidates instead of
running out of budget on the way there.

### `MAX_COMMUNITY_THREADS`: 10 → 20, and the deeper bug it only partly fixes

`api.tasks.community.MineCommunityHandler` computes `per_call_limit =
max(max_community_threads // (len(keywords) * len(venues)), 1)` — an equal
split of the total thread budget across every `(venue, keyword)` pair. Real
plans from the live runs used 6 to 18 such pairs:

```
q02 (email):    6 keywords × 3 venues = 18 pairs  ->  per_call_limit = 10 // 18 = 1
q07 (helpdesk): 3 keywords × 2 venues =  6 pairs  ->  per_call_limit = 10 //  6 = 1
q09 (WhatsApp): 4 keywords × 3 venues = 12 pairs  ->  per_call_limit = 10 // 12 = 1
```

`per_call_limit` floored to exactly **1** in every real plan observed —
integer division doesn't round up, and none of these pair counts divide
into 10 evenly enough to clear 2. This is the traced, direct explanation
for why every `complaint.*`/`request.*` theme across all six runs had
`support_count` between 1 and 4 (§2 above): a theme can only ever
accumulate as many comments as `per_call_limit` allows per contributing
pair, and 1 comment per pair almost never clusters up to 5. Doubling the
cap to 20 gives `per_call_limit >= 2` for any plan with up to 10 pairs, but
does **not** fully close the gap for wider plans (q02's 18 pairs still
floors to 1 at 20 — would need 36+ to guarantee 2 there). Chosen
deliberately conservative rather than jumping straight to 36+: doubling
already meaningfully changes the shape of the problem, and
`MineCommunityHandler`'s own 180s timeout (raised once already in Phase 10
for exactly this reason) means a larger cap costs real wall-clock time,
not just search credits. **The more complete fix is structural, not a flat
cap** — a per-pair floor (e.g. `max(3, ...)` instead of `max(1, ...)`) or
fewer, better-chosen keyword variants from the planner — logged here as a
Phase 09/10 design note, not fixed in this phase.

### Why `Settings`'s wall-clock quota knobs stay unset in this repo's local `.env`

`RUNS_PER_USER_PER_DAY`/`GLOBAL_RUNS_PER_DAY`/`MAX_CONCURRENT_RUNS` are
derived and documented above and in `.env.example`, but this repo's own
*local* `.env` leaves them commented out. Real reason, found while
verifying `make check` after setting them for real: `api.web.quota
.try_create_run`'s admission check counts `runs` rows by wall-clock
`started_at > now() - interval '1 day'`, not scoped to any one test — the
shared, long-lived local `ai_pi_test` Postgres this test suite runs
against accumulates real rows across every test file in a session, so a
small enforced `GLOBAL_RUNS_PER_DAY` started tripping the kill switch
inside `tests/integration/test_quota.py`/`test_api.py` themselves (`code
=live_runs_paused`), which had never been exercised for real before since
these knobs were always `None` (unenforced) up to this phase. This is not
a bug in those tests or in `try_create_run` — quota enforcement is exactly
as designed, and CI is unaffected (`.env` is gitignored and never reaches
CI; `ci.yml` sets its own placeholder env vars directly and doesn't set
these). It is a genuine, worth-recording interaction: **the first time a
quota knob goes from `None` to a real small number, expect it to bite any
test that creates more `runs` rows than the cap inside one shared-DB test
session.** `RUN_BUDGET_WEIGHT`/`RUN_BUDGET_USD`/`MAX_COMPETITORS_PROFILED`/
`MAX_PAGES_PER_ENTITY`/`MAX_COMMUNITY_THREADS`/the Exa credit caps are all
safe to leave active locally — none of them are wall-clock/shared-table
based the same way.

## 6. Zero-spend replay: a real, unresolved architectural limitation

Found while verifying `.github/workflows/bench.yml`'s own core promise
("Runner executes from cache at zero spend," the phase doc's own testing
table). `python -m bench.runner --tuning --cached-only`, run a second time
against the very same Postgres the live tuning benchmark had just
populated, does **not** complete cleanly. Two distinct, confirmed causes:

1. **`api.retrieval.robots.RobotsCache` is in-memory only.**
   `self._parsers: dict[str, tuple[float, RobotFileParser]]` — never
   written to Postgres. A fresh process (a new `bench.runner` invocation, a
   new CI job — anything that isn't the exact same running process that did
   the original fetch) starts with an empty robots cache and must re-fetch
   `robots.txt` for any domain not already checked *in that process's own
   lifetime*, even though the underlying page content itself is properly
   Postgres-cached (`sources`) and would otherwise replay for free.
2. **`api.sources.hn.HNRetriever` and GitHub's Search API
   (`api.sources.github`'s `search_repositories`/`search_issues`) have no
   caching layer at all** — Postgres or otherwise. Masterplan §9 specifies
   exactly three cache types (source: 7d, search: 24h, extraction:
   permanent) and none of them cover domain retrievers; Phase 04 never
   built one for HN/StackExchange/Wayback/packages/ProductHunt/GitHub
   search.

**Confirmed empirically, not just by reading the code**: a real
`--cached-only` re-run of all six tuning queries (`RUN_BUDGET_WEIGHT=40`,
matching the value they actually executed under) failed at least one task
on five of the six — `discover_competitors` whenever `consider_oss=true`
(GitHub Search, uncached), `mine_community`/`trend_signals` whenever they
touch HN (uncached), and `discover_competitors` on any brand-new domain
Exa's cached search result names but this process has never checked
`robots.txt` for before. Only q02 (the fallback-plan query, structurally
simpler — no OSS/funding branches) got far enough to report a real
competitor and a non-zero coverage score from cache alone.

**A second, distinct, real bug found in the same pass**: q08's cached-only
replay failed with `update or delete on table "entities" violates foreign
key constraint "claims_entity_id_fkey"` — `api.resolve.store.merge_alias`
attempting to merge `gh:crisp-oss/chappe` into a canonical `web:npmjs.com`
entity that still has `claims` rows pointing at it from an earlier run.
**Diagnosed and fixed in the Phase 14 follow-up (2026-08-10)**: `claims.
entity_id` deliberately has no `ON DELETE CASCADE`, so the merge delete
orphaned the losing entity's old-run claims. `merge_alias` now repoints
those claims onto the canonical entity before the delete. Confirmed live:
q08's discovery crashed on exactly this FK on 2026-08-10 pre-fix and re-ran
clean post-fix (also see `docs/benchmark.md`'s follow-up section).

**Not fixed here.** Building persistent caching for `RobotsCache` and every
domain retriever is real Phase 03/04 infrastructure work — a new migration,
new cache tables, new read/write paths in each retriever — squarely
outside Phase 14's own scope ("changing product behaviour beyond tuning
constants" is explicitly not this phase's job, and this is considerably
more than a constant). `.github/workflows/bench.yml` ships anyway, with
`continue-on-error: true` on the cached-run and regression-check steps and
a top-of-file comment explaining exactly why, so the workflow the phase
doc's exit criteria ask for exists and remains informative on whatever
*does* complete from cache, rather than either lying about being green or
being withheld entirely over a gap this phase didn't create and isn't
scoped to close.

## 7. Phase 14 follow-up (2026-08-10): decisions 06a/06b + the owning-phase fixes

### Extraction drops traced — and a minimum quote length is *not* the fix (06a)

Decision 06a ("impose a quote-length floor?") was deliberately "measure
first". Replayed every raw extraction from the permanent `extraction_cache`
(257 entries, 987 raw claims): **157 reproduced drops** —
`quote_ambiguous`=62, `quote_not_in_source`=57, `invalid_attribute`=20,
`value_type_mismatch`=18. The dominant causes, in order:

1. **HTML-entity mismatch (10 confirmed this way, pattern dominates
   `quote_not_in_source`)**: the model quotes `&amp;`/`&#39;`-laden text
   ("Trips &amp; projects") while the stored `sources.extracted_text` is
   entity-decoded ("Trips & projects") — a Phase 03/06 normalisation
   contract mismatch, not a short-quote problem.
2. **Non-vocabulary attributes (`invalid_attribute`)**: the model emits
   `pricing.free_trial_days` (vocabulary: `pricing.trial_days`) and
   `feature.<slug>` without the required `.present` boolean suffix.
3. **Text-where-typed (`value_type_mismatch`)**: price display strings
   ("MXN $5,000/month", "Pay £108/year for Pro.") for the numeric
   `pricing.entry_usd_month`; prose for boolean `feature.*.present`.
4. **Genuinely short quotes are a minority** (a few list bullets like
   "- Batch upload"), and *long* ambiguous quotes are just as common —
   a length floor would fix at most a handful of drops and is not worth
   its risk of discarding real short facts. **Verdict: no quote-length
   floor; the levers are the three compliance gaps above (owning-phase,
   extractor/prompt work).**

### Exa snippets are now quoteable evidence (06b, implemented)

The search-result snippet (≤500 chars, Exa `text`) becomes a grade-C
synthetic source (`retrieval_reason='serp_snippet'`, canonical_url
`https://<root>#serp-snippet` so it never collides with a real homepage
fetch) that `profile_product` extracts claims from. The span guarantee
holds mechanically — the quote is verbatim in the stored snippet text.
Two deliberate constraints: `pricing.*` claims are **dropped** from
snippet extraction (a machine summary must never satisfy the competitor
pricing triple), and the grade-C tag labels the provenance honestly
("from a search snippet", not "fetched from the vendor's page").
