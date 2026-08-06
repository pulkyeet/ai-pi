# Phase 01 — External Dependency Validation Spike

| | |
|---|---|
| **Depends on** | [00](phase-00-foundation-contracts-ci.md) |
| **Unlocks** | [03](phase-03-fetch-source-cache.md), [04](phase-04-search-domain-retrievers.md), [05](phase-05-llm-gateway.md) |
| **Milestone** | No |
| **Concrete output** | `docs/external_apis.md` — a go/no-go table with **measured** limits, latencies and costs per vendor; plus a committed fixture corpus every later phase tests against |

---

## Objective

Prove every external dependency works, and record what it actually costs and limits, **before** any code is built on top of it. Nothing in this phase ships to production. The output is knowledge plus fixtures.

## Why this phase exists

This is the deviation-risk phase. The masterplan was written against a world that has already moved: Brave's free tier is gone, Google CSE is closed to new customers, Fly.io has no free tier, arq cannot run without Redis, and Reddit no longer self-serves API credentials.
 Four of those five are cost or viability blockers, and every one of them would have been discovered mid-build — after code depended on it.

Two days of throwaway scripts here is the cheapest insurance in the plan. The rule: **no phase may build on a vendor that has not passed its Phase 01 smoke test.**

---

## Scope

### In

- One throwaway smoke script per vendor under `spikes/`, each printing measured facts
- Credential acquisition for every service (including the ones with lead time)
- HTTP fixture capture for the deterministic test corpus
- A written go/no-go decision per vendor with the numbers behind it

### Out

- Any production code. Everything in `spikes/` is deleted or archived at phase end; only `docs/external_apis.md` and `tests/fixtures/` survive.
- Abstractions. Do not build the search interface here — [Phase 04](phase-04-search-domain-retrievers.md) builds it, informed by what this phase learns.

---

## Deliverables

```
spikes/                            # throwaway, archived at phase end
├── search_exa.py
├── llm_openrouter.py
├── crawl_static.py                # httpx + trafilatura across 40 real pricing pages
├── crawl_js.py                    # Playwright on the pages static crawl failed
├── github_api.py
├── hn_algolia.py
├── producthunt.py
├── wayback_cdx.py
├── packages.py                    # npm + PyPI
└── stackexchange.py
docs/external_apis.md              # THE deliverable
tests/fixtures/cassettes/*.yaml    # VCR recordings
tests/fixtures/pages/*.html        # raw HTML + expected extraction
```

---

## Design

### Credential acquisition — start the slow ones on day one

Two have lead time and must be requested **before** any smoke testing begins:

| Service | Lead time | Action |
|---|---|---|
| **Reddit** | 2–4 weeks, manual approval | Apply immediately. Self-service registration is closed ([D5](README.md#deviations-from-the-masterplan)). Treat as *may never arrive*. |
| **Product Hunt** | Minutes, but token-gated | Register an app, obtain a developer token. |

Everything else is instant: Exa, OpenRouter, GitHub PAT.

**Reddit is explicitly not on the critical path.** Design the community-mining branch ([Phase 04](phase-04-search-domain-retrievers.md)) around HN Algolia + GitHub Issues + Stack Exchange, all of which are free, unauthenticated or instantly-keyed, and unrestricted. If Reddit credentials arrive, it becomes an additional source. If they do not, the run reports a coverage gap — which the masterplan's `coverage` field already models. No redesign either way.

### Search provider characterisation

The masterplan's provider choices are both unavailable ([D1](README.md#deviations-from-the-masterplan), [D2](README.md#deviations-from-the-masterplan)). **Exa is decided** — it is the only remaining provider that is both zero-marginal-cost (recurring $10/mo credit, plus a $20 signup credit) and strong on the thin, badly-keyworded queries this project depends on.

So this is no longer a vendor bake-off. It answers two questions about a provider already chosen: **is Exa's recall good enough**, and **how much of the monthly allowance does one run actually burn?** The second matters more than it looks — an allowance, unlike metered billing, fails as a cliff rather than a cost.

**Fixed evaluation set** — 12 queries spanning the difficulty range the benchmark will use ([Phase 14](phase-14-benchmark-calibration.md)): 4 mainstream SaaS categories, 4 mid-difficulty, 4 thin/niche (`"MEV monitoring for solo validators"`, `"WhatsApp first CRM for Indian SMBs"`). Thin queries are the discriminator — every provider looks fine on `"project management tool"`.

**Measure per query:** result count, wall-clock latency (p50/p95 over 3 runs), whether the top 10 contain the known-correct competitors for that category, whether it returns anything usable at all on thin queries, observed rate-limit behaviour under a 10-request burst, and — new, and required — **credit consumed per query**, including how Exa's search modes and any content-retrieval options differ in cost. Record the units Exa bills in, not just the count of calls.

**Derive and record in `docs/external_apis.md`:** credits per query by mode, projected credits per run at the ~8-query estimate, and the implied ceiling on runs per month against the $10 allowance. That number is the input to `GLOBAL_RUNS_PER_DAY` in [Phase 14](phase-14-benchmark-calibration.md), and it is the difference between the free tier holding and the project quietly going dark mid-month.

**Exit condition, not a vendor choice.** Exa ships regardless; what this phase decides is whether the allowance supports the intended run volume, and if not, which lever moves — harder path-guessing ([Phase 03](phase-03-fetch-source-cache.md)), a tighter per-run search cap, or a lower global daily quota.

### Crawl viability — resolving masterplan open item #3

Masterplan §14 asks whether Playwright is needed at all. Answer it with data, not opinion.

Take **40 real pricing pages** across the categories the benchmark will cover. For each: fetch with `httpx` + `trafilatura`, and record whether the extracted text contains a recognisable price. Then run the failures through Playwright and see how many it recovers.

Report three numbers:

- **Static hit rate** — % of pages where httpx + trafilatura alone yields a price
- **Playwright recovery** — % of static failures that Playwright fixes
- **Cost of Playwright** — container image size delta, cold-start latency, memory ceiling

Decision rule: if static hit rate ≥ 80% (the masterplan's §12.10 assumption), Playwright is **deferred behind a feature flag**, not built. It ships only if Phase 14 benchmark recall is demonstrably limited by JS-rendered pricing pages. This keeps the deployment image small and the Fly machine size — and therefore the bill — down ([D4](README.md#deviations-from-the-masterplan)).

Also measure **path-guessing hit rate** here, since the same 40 pages answer it: for each known domain, do `/pricing`, `/plans`, `/pricing-plans` resolve to a 200 containing a price? This number is the single biggest driver of per-run search volume ([README cost model](README.md#cost-model)) — if it comes in low, search volume roughly quadruples, and against Exa's fixed monthly allowance that means the sustainable run ceiling drops by the same factor. The whole quota model shifts with it.

### LLM validation

Three things to verify about `deepseek/deepseek-v4-flash` via OpenRouter, all of which the masterplan asserts (§6) and none of which should be taken on faith:

1. **Structured output actually holds.** Send 50 extraction-shaped requests with a strict JSON schema. Count schema violations. The masterplan warns that without `require_parameters: true` a request can be routed to a backend that silently ignores `response_format` — verify that provider pinning fixes it, and record the violation rate with and without.
2. **Prompt caching works and pays.** Structure a request with the static claim schema as prefix. Fire it 10 times. Read back cache-hit token counts from the response usage. Confirm cache reads are billed at the claimed discount. The masterplan's ~4× saving on extraction depends entirely on this.
3. **Real cost per extraction call.** Run 20 real pages through a realistic extraction prompt. Record actual input tokens, output tokens, and dollars. Extrapolate to a 60-page run and compare against the masterplan's ~$0.03/run estimate.

Also record p50/p95 latency per call — [Phase 14](phase-14-benchmark-calibration.md) needs it to derive `RUN_TIMEOUT_S`, and the masterplan's "under three minutes" promise lives or dies on it.

If the model ID or pricing has moved, record what is actually available and re-derive the cost model rather than assuming the masterplan's numbers.

### Free-API smoke tests

For each, confirm reachability without auth (or with the free key), record the real rate limit from response headers, and capture a fixture:

| Source | Verify |
|---|---|
| GitHub REST/GraphQL | 5,000 req/hr with PAT; `is:issue label:enhancement sort:reactions-desc` returns reaction counts; star history available for 90-day velocity |
| HN Algolia | No auth; observed rate limit; search by date and by relevance |
| Wayback CDX | Snapshot listing for a domain; latency (it is slow — measure it) |
| npm / PyPI | Download counts endpoint; exact numbers, grade A per masterplan §5 |
| Stack Exchange | Free quota (headers report remaining); filter syntax for question bodies |
| Product Hunt GraphQL | Token works; launch date, tagline, upvotes retrievable |

### Fixture capture

Every smoke test records its HTTP traffic via VCR.py into `tests/fixtures/cassettes/`. These become the deterministic corpus for every later phase's integration tests — CI replays them, costs nothing, and never flakes on a vendor outage.

Rules for the corpus:

- **Scrub secrets** — a VCR filter strips `Authorization`, `X-API-KEY`, and any token query params before writing. Verified by a test that greps the committed cassettes for key-shaped strings.
- **Capture failure modes too** — a 429, a 403 from an anti-bot page, a timeout. [Phase 03](phase-03-fetch-source-cache.md) and [Phase 04](phase-04-search-domain-retrievers.md) need these to test retry paths without waiting for a real outage.
- **Date-stamp everything.** Cassettes rot. Each gets a `recorded_on` note, and a nightly `@pytest.mark.live` job re-hits the real endpoint to detect drift.

---

## Testing

This phase is itself a test, so "testing" means: does the evidence hold up?

| Kind | What |
|---|---|
| Reproducibility | Every smoke script is re-runnable and prints the same conclusion twice in a row. A one-off result in a terminal scrollback is not a finding. |
| Fixture integrity | Automated check: no committed cassette contains a credential-shaped string. |
| Fixture replay | Each cassette replays cleanly through VCR in `none` record mode — proving later phases can actually use it offline. |
| Live drift | `tests/live/test_vendors.py` hits each real endpoint and asserts the shape still matches its cassette. Marked `@pytest.mark.live`, nightly, non-blocking. |
| Statistical honesty | Latency figures are p50/p95 over ≥ 3 runs, not a single sample. Recall figures state N. |

---

## `docs/external_apis.md` — required contents

The deliverable is only useful if it is specific. Required structure:

```markdown
# External API Reality Check
Recorded: <date>. Re-verify before deployment.

## Go / No-Go
| Vendor | Verdict | Free allowance | Marginal cost | Measured p95 | Blocker |

## Search bake-off
| Provider | Thin-query recall (N=4) | Mainstream recall (N=4) | p95 latency | $/query | Verdict |

## Crawl
- Static (httpx+trafilatura) hit rate: __% of 40 pages
- Path-guess (/pricing,/plans,...) hit rate: __%
- Playwright recovery on failures: __%
- **Decision: Playwright [ships / deferred behind flag]** — because ...

## LLM
- Model available: <id>. Priced at: $__/M in, $__/M out, $__/M cache read
- Schema violation rate: __/50 without provider pinning, __/50 with
- Prompt cache hit confirmed: yes/no. Measured saving: __%
- Measured cost for a 60-page run: $____ (masterplan estimated $0.03)
- p50 / p95 per extraction call: __ms / __ms

## Rate limits (measured, from response headers)
| Source | Limit | Window | Auth |

## Credentials
| Service | Status | Obtained | Notes |
```

---

## Exit criteria

- [ ] Every vendor has a go/no-go verdict backed by a number, not an impression
- [ ] Exa credits-per-query measured by mode, and projected credits per run recorded
- [ ] Implied monthly run ceiling against the $10 allowance derived and written down
- [ ] Playwright decision made and written down with its hit-rate evidence
- [ ] Masterplan §14 open item #3 (is Playwright needed?) is **closed**
- [ ] LLM structured-output violation rate measured with and without provider pinning
- [ ] Prompt caching confirmed working, with measured saving
- [ ] Real per-run LLM cost measured and compared against the $0.03 estimate
- [ ] Reddit credentials **applied for** (not necessarily granted)
- [ ] Fixture corpus committed; secret-scrub test passes; all cassettes replay offline
- [ ] `docs/external_apis.md` complete and dated
- [ ] Any new deviation found is added to the [README deviation table](README.md#deviations-from-the-masterplan)

---

## Risks

| Risk | Mitigation |
|---|---|
| A vendor changes again mid-build | Nightly live drift tests catch it. `external_apis.md` is dated and re-verified before [Phase 15](phase-15-deployment-observability.md) deploy. |
| Exa thin-query recall is poor | Measured here, before anything depends on it. With no second provider in v1 ([README](README.md#deviations-from-the-masterplan)), the fallback is not another vendor but leaning harder on domain retrievers (HN Algolia, GitHub, Stack Exchange) and path-guessing, which are independent of Exa entirely. |
| Exa outage or allowance exhaustion mid-month | Discovery degrades; runs do not stop. Domain retrievers and path-guessing carry a reduced run, and the report's `coverage` field already models a gap. The per-run search cap and `GLOBAL_RUNS_PER_DAY` exist to make exhaustion unlikely rather than to recover from it. |
| Static crawl hit rate well below 80% | Playwright ships after all, and the deployment image grows — which on Fly means a larger machine and a slightly higher bill. Cost impact quantified here rather than discovered at deploy. |
| Path-guess hit rate low | Search volume per run rises ~4×, which against a fixed monthly allowance means the run ceiling drops ~4× — the real cost is quota, not dollars. Feeds directly into [Phase 14](phase-14-benchmark-calibration.md) quota derivation. |
| DeepSeek V4 Flash unavailable or repriced | OpenRouter is a gateway; swapping model IDs is config. Re-run the LLM validation against the next-cheapest structured-output-capable model and update the cost model. |
| Spike code leaks into production | Explicit exit criterion: `spikes/` is archived, and no `src/api/` module imports from it. Enforced by a lint rule. |

## Open decisions

1. **Does a second search provider ship later?** v1 is Exa-only, accepting the loss of index independence ([README](README.md#deviations-from-the-masterplan)). If this phase measures thin-query recall gaps that the domain retrievers do not cover, revisit — but as a Phase 14 decision with benchmark evidence, not a v1 hedge.
2. **Cassette refresh cadence.** Nightly drift detection is agreed; unresolved is whether a drift failure auto-opens an issue or just alerts.
