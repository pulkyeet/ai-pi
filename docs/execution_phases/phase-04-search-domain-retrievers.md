# Phase 04 — Search & Domain Retrievers

| | |
|---|---|
| **Depends on** | [00](phase-00-foundation-contracts-ci.md), [01](phase-01-dependency-validation-spike.md), [03](phase-03-fetch-source-cache.md) |
| **Unlocks** | [10](phase-10-task-handlers-e2e.md) |
| **Milestone** | No |
| **Concrete output** | One `Retriever` interface over web search and every structured source, with a hard per-run search budget that cannot be exceeded and per-provider quota accounting |

---

## Objective

Give the system every way it has of finding things, behind one interface, with search treated as the scarce resource it now is.

## Why the design changed from the masterplan

The masterplan's search plan is no longer buildable. Brave killed its free tier in Feb 2026 ([D1](README.md#deviations-from-the-masterplan)) and Google CSE is closed to new customers and retires in Jan 2027 ([D2](README.md#deviations-from-the-masterplan)). Both named providers are out.

What survives intact is the masterplan's actual insight (§7): **most of your searches should not be searches.** Domain-specific retrievers and deterministic path guessing replace general search wherever they exist, which is most places. That principle now matters more, not less — search went from "free tier with usable headroom" to "metered from the first query".

Concretely, per run: ~6–10 web searches for *discovery only*, then everything else through free structured APIs and direct fetches.

---

## Scope

### In

- `SearchProvider` protocol with Exa as the single v1 provider ([D1](README.md#deviations-from-the-masterplan))
- Allowance tracking against Exa's monthly credit, plus a global daily cap
- Graceful degradation when search is unavailable or the allowance is exhausted
- Search result caching, 24h TTL, keyed per masterplan §9
- Domain retrievers: GitHub, HN Algolia, Wayback CDX, npm/PyPI, Stack Exchange, Product Hunt
- SERP-snippet reading for anti-bot aggregators (G2, Capterra) — never crawled
- Per-run `RetrievalBudget` enforced at the call site

### Out

- Deciding *what* to search for ([Phase 09](phase-09-interpreter-planner.md) plans; [Phase 10](phase-10-task-handlers-e2e.md) orchestrates)
- Page fetching ([Phase 03](phase-03-fetch-source-cache.md))
- Interpreting results into claims ([Phase 06](phase-06-claim-extraction-span-binding.md))
- Reddit as a required source — dropped, see below

---

## Deliverables

```
src/api/search/
├── __init__.py
├── base.py            # SearchProvider protocol, SearchResult, SearchResponse
├── exa.py
├── router.py          # provider selection, allowance tracking, degradation
├── cache.py           # 24h result cache
└── budget.py          # RetrievalBudget
src/api/sources/
├── __init__.py
├── base.py            # Retriever protocol
├── github.py
├── hn.py
├── wayback.py
├── packages.py        # npm, PyPI
├── stackexchange.py
├── producthunt.py
├── serp_snippets.py   # G2/Capterra via SERP, never crawled
tests/unit/, tests/integration/, tests/live/
```

---

## Design

### The retrieval budget

The scarcest resource in the system, so it is a first-class object rather than a convention:

```python
class RetrievalBudget:
    max_searches: int          # per run, hard cap
    max_fetches: int
    def spend_search(self, provider: str) -> None:  # raises BudgetExhausted
    def spend_fetch(self) -> None:
```

Every provider call goes through it. Exhaustion raises a typed error the task handler catches, converting it into a coverage gap rather than a run failure — consistent with the masterplan's "partial failure is the normal case".

Three tiers of limit, all enforced independently:

1. **Per-run** — `RetrievalBudget`, bounds one run
2. **Daily** — protects Exa's monthly allowance from being drained by a single bad day
3. **Global daily** — masterplan §8.2's `GLOBAL_RUNS_PER_DAY`, derived not guessed:

```
GLOBAL_RUNS_PER_DAY ≈ (monthly_search_credits / 30) / searches_per_run_p95
```

Note **p95, not median** — the masterplan is explicit that the tail is what drains an allowance. All three values stay `TBD` until [Phase 14](phase-14-benchmark-calibration.md) measures real per-run search counts, with the numerator coming from Exa's measured credits-per-query ([Phase 01](phase-01-dependency-validation-spike.md)).

**Track credits, not calls.** Exa bills in credits and different search modes cost differently, so a counter of queries issued is not a measure of allowance consumed. The budget layer records credits, reads the running monthly total, and treats the allowance as the real ceiling. This is the substantive difference from a metered provider: overspending does not produce a larger bill, it produces an outage.

### Search provider abstraction

```python
class SearchProvider(Protocol):
    name: str
    async def search(self, query: str, *, limit: int = 10,
                     site: str | None = None) -> SearchResponse: ...
```

`SearchResponse` carries normalised results (`url`, `title`, `snippet`, `rank`, `provider`) **plus the credits the call consumed** — the budget layer cannot do its job if cost is not reported back with the results. Provider-specific extras go in an opaque `raw` field that nothing outside the provider module reads.

**Keep the protocol even with one provider.** Exa is the only implementation in v1, and the abstraction is still worth its weight: it is what makes adding a second provider a new file rather than a refactor, and it is what lets tests replay search without touching a vendor. It costs one file.

**One provider, and the trade-off is deliberate.** The earlier plan paired two providers on independent indexes so a single index gap or outage could not blind the system. Exa-only gives that up in exchange for zero marginal cost and better thin-query recall ([README](README.md#deviations-from-the-masterplan)). What makes it survivable is that **the domain retrievers below are entirely independent of Exa** — HN Algolia, GitHub, Stack Exchange, Wayback and the package registries keep working through an Exa outage, so a run degrades to reduced discovery rather than failing.

**Degradation is a designed path, not an error path.** When Exa is unavailable or the allowance is exhausted: log it, mark the affected run's `coverage` field, continue with domain retrievers and path-guessing, and let the report say what it could not see. The report contract models coverage gaps, so nothing new is needed to express it.

**Caching** per masterplan §9: key `hash(query + provider + params)`, 24h TTL, **shared across users**. This is what makes a second query in an already-explored category nearly free, and it is one of the two levers (with path guessing) that make the global daily cap workable.

### Domain retrievers — replacing search where possible

Each implements a common `Retriever` protocol and returns typed records. All are free.

| Retriever | Yields | Grade | Notes |
|---|---|---|---|
| **GitHub** | repo metadata, stars, 90-day star velocity, last commit, license, contributors, issues by reactions | A (API) / D (issue comments) | 5,000 req/hr with PAT. The best *structured* pain-point source available. |
| **HN Algolia** | launches, comments, post volume over time | D | No auth. Also yields trend signal for free. |
| **Wayback CDX** | historical snapshots — "when did X ship", pricing history | B | Slow; call it sparingly and with a generous timeout. |
| **npm / PyPI** | download counts as adoption proxy | A | Exact numbers, free, no auth. |
| **Stack Exchange** | developer pain points | D | Free quota; remaining reported in response headers. |
| **Product Hunt** | launch date, tagline, upvotes | B | GraphQL, free token. |
| **SERP snippets** | G2/Capterra/GetApp review and pricing bands | C | **Read via search snippets. Never crawled** — masterplan §5. |

**GitHub deserves emphasis.** `is:issue label:enhancement sort:reactions-desc` is a literal feature-request leaderboard with counts and permalinks — reaction-weighted, so one issue with 47 thumbs-up clears the promotion bar where one community comment never does (masterplan §4.6). `awesome-<category>` repos are hand-curated competitor sets and very high-precision discovery seeds. Star velocity over 90 days, not total stars, is the real adoption signal.

GitHub is **planner-gated** (masterplan §5): "does this category plausibly have OSS competitors" is exactly the judgement that justifies having a planner at all. This phase builds the retriever; [Phase 09](phase-09-interpreter-planner.md) decides when to use it.

### Reddit — dropped as a source

The masterplan initially planned Reddit as a Tier-2 source ("search only, never bulk", §5/§13). Reddit's free tier still exists (100 QPM, non-commercial) but **self-service registration is closed**; new credentials need manual approval, typically 2–4 weeks ([D5](README.md#deviations-from-the-masterplan)). That manual approval process made the source infeasible, so **Reddit is dropped** — no `reddit.py`, no `ENABLE_REDDIT`, no credentials. The community-mining branch is complete without it: HN Algolia, GitHub Issues and Stack Exchange are the backbone.

Search hits that point at reddit pages are still handled as ordinary page content (a reddit page can be fetched as evidence for a non-Reddit entity; it is just never mined through a Reddit API).

### Rate limiting and politeness

Each retriever declares its own limit, taken from measured [Phase 01](phase-01-dependency-validation-spike.md) values rather than documentation. A shared token-bucket limiter per service enforces it. Where a vendor reports remaining quota in headers (Stack Exchange, GitHub), that is read and used as ground truth — reacting to the actual counter beats predicting it.

---

## Testing

| Kind | Test | Asserts |
|---|---|---|
| Unit | Budget exhaustion raises `BudgetExhausted` at exactly the cap; never at cap−1 | Hard limit is hard |
| Unit | Cache key stability: same query + provider + params → same key; param order irrelevant | Cache actually hits |
| Unit | `SearchResponse` normalisation from Exa's raw payload | Provider details do not leak |
| Unit | Credit accounting sums per-call costs correctly, including differing search modes | The allowance ceiling is measured, not estimated |
| Integration | Every retriever against committed cassettes | Offline, deterministic, free |
| Integration | Search cache: second identical query makes zero network calls | Mock transport fails on any request |
| Integration | Exa 5xx/timeout → run continues on domain retrievers, `coverage` marked | Degradation is a designed path |
| Integration | Allowance exhausted → typed error → coverage gap, **not** a crashed run | Partial-failure semantics |
| Integration | GitHub retriever parses reaction counts, star velocity, license, last-commit correctly | The highest-value structured source |
| Integration | SERP-snippet path for G2/Capterra never issues a fetch to those domains | Anti-bot compliance, asserted by request interception |
| Integration | Rate limiter serialises bursts per service with correct spacing | Fake clock timing assertion |
| Live (nightly) | Each retriever against the real API; shape matches cassette | Cassette-drift detection |
| Live (nightly) | Phase 01 evaluation queries re-run against Exa; recall compared to baseline | Provider quality regression alarm |

---

## Exit criteria

- [ ] `SearchProvider` implemented for Exa, with credits reported back on every call
- [ ] Exa unavailability and allowance exhaustion both degrade to a coverage gap, proven end-to-end
- [ ] `RetrievalBudget` cannot be exceeded — tested at the boundary
- [ ] Daily and global daily caps implemented in credits, not query counts (values `TBD` until [Phase 14](phase-14-benchmark-calibration.md))
- [ ] Search cache hits across runs and across users
- [ ] All seven domain retrievers implemented, typed, and cassette-tested
- [ ] G2/Capterra reachable only via SERP snippets; direct fetch impossible by construction
- [ ] Reddit dropped as a source (D5); no feature flag or retriever ships
- [ ] Every retriever declares a rate limit sourced from measurement, not docs
- [ ] Full suite runs offline
- [ ] Coverage ≥ 85% on `src/api/search/` and `src/api/sources/`

---

## Risks

| Risk | Mitigation |
|---|---|
| Search usage exceeds the monthly allowance | Three independent caps (run, daily, global-daily) counted in credits, plus the 24h shared cache. Exhaustion is bounded by construction, and degrades rather than crashes. |
| Exa changes pricing or kills the free credit | The same failure that took out Brave and Google CSE, so treat it as likely rather than hypothetical. The provider abstraction makes swapping a new file; the domain retrievers are unaffected; nightly live tests detect drift. A replacement bake-off would be a day's work, not a redesign. |
| Exa outage blinds discovery | Domain retrievers are independent of it and keep working. Runs degrade to reduced coverage, which the report contract already models. |
| GitHub 5,000/hr exhausted on a busy day | Header-reported remaining quota is ground truth; retriever degrades to cached data and reports a coverage gap. |
| Wayback is slow and drags run latency | Generous timeout, low priority, planner-gated. A Wayback timeout is a coverage gap, never a blocked run. |
| Reddit never approved | Resolved by dropping Reddit (D5): the manual approval process was infeasible, so the source does not ship. |
| Aggregator SERP snippets too thin to be useful | Grade C by design; masterplan already treats them as weak evidence. If they add nothing measurable in [Phase 14](phase-14-benchmark-calibration.md), drop the source rather than crawl it. |

## Open decisions

1. **Trend signal source.** Masterplan §5 proposes Wikipedia pageviews plus HN post volume derived from results already in the pipeline. Is HN volume alone enough signal, or does Wikipedia pageviews need to carry it? Decide with [Phase 14](phase-14-benchmark-calibration.md) data.
2. **Snippet-only claims.** Should a claim sourced purely from a SERP snippet be admissible, given span binding ([Phase 06](phase-06-claim-extraction-span-binding.md)) requires the quote to exist in fetched text? A snippet *is* fetched text of a sort, but it is short and provider-normalised. Proposal: admissible at grade C with the snippet stored as the source text. Confirm in [Phase 06](phase-06-claim-extraction-span-binding.md).
