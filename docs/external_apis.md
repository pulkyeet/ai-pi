# External API Reality Check

Recorded: 2026-08-06. Re-verify before deployment (see [Phase 15](execution_phases/phase-15-deployment-observability.md)).

Produced by [Phase 01](execution_phases/phase-01-dependency-validation-spike.md); the "Fetch &
path-guessing" section below was added by [Phase 03](execution_phases/phase-03-fetch-source-cache.md).
All numbers below are measured, not estimated — see `spikes/` for the scripts and
`tests/fixtures/cassettes/` + `tests/fixtures/pages/` for the recorded evidence.
`tests/live/test_vendors.py` re-checks response shape nightly so this document doesn't silently rot.

## Go / No-Go

| Vendor | Verdict | Free allowance | Marginal cost | Measured p95 | Blocker |
|---|---|---|---|---|---|
| Exa (search) | **GO** | $20 signup + $10/mo recurring | $0.007/query (base), +$0.01 for `summary` content | ~740ms (worst query-level p50 observed; typical ~270ms) | None |
| OpenRouter — deepseek/deepseek-v4-flash | **GO**, with a usage rule | none (metered) | $0.0000000882/tok in, $0.0000001764/tok out | 14.7s (schema-constrained); 62.1s (free-form, avoid) | Must always use `response_format` schema constraints — see LLM section |
| GitHub REST/GraphQL (issues, search, rate limit) | **GO** | 5,000 req/hr (PAT), 30 req/min (search endpoint specifically) | $0 | 691ms | None |
| GitHub Starring endpoint (star velocity) | **NO-GO** | n/a | n/a | n/a | Restricted since 2026-06-30 to repo **admins and collaborators only**; fine-grained PATs are not supported for it at all (no fine-grained permission exists), and a classic PAT with `public_repo` works only when its owner is an admin/collaborator of the target repo — never true for competitor repos. Only total `stargazers_count` remains readable; per-star timestamps for 90-day velocity are unavailable for arbitrary repos. |
| HN Algolia | **GO** | unlimited, unauthenticated | $0 | 648ms | None |
| Wayback CDX | **GO, with a caveat** | unlimited, unauthenticated | $0 | 3076ms | Genuinely slow — keep off any latency-sensitive path; run in background/async only |
| npm downloads API | **GO** | unlimited, unauthenticated | $0 | 272ms | None |
| PyPI downloads (via pypistats.org) | **GO** | unlimited, unauthenticated | $0 | not separately measured (single call, fast) | None |
| Stack Exchange | **GO** | 300 req/day, anonymous | $0 | 350ms | Quota is body-only JSON (`quota_max`/`quota_remaining`), never headers — masterplan text implied headers; code must poll the body |
| Product Hunt GraphQL | **GO** | free developer token | $0 | not yet measured | Developer token obtained 2026-08-07; `post_by_slug` verified live and cassette-recorded. **No text-search field exists in Product Hunt v2 GraphQL** (query root: `collection`/`collections`/`comment`/`post`/`posts`/`topic`/`topics`/`user`/`viewer` — verified by schema introspection) — the retriever is slug-lookup only. |
| Reddit | **DEFERRED (per D5)** | n/a | n/a | n/a | Application **not yet submitted** in this pass (2–4 week manual approval once it is). HN Algolia + GitHub + Stack Exchange are the community-mining backbone regardless — see [README](execution_phases/README.md#deviations-from-the-masterplan). |
| Playwright | **Deferred behind a feature flag** | n/a | ~300–400MB image size delta, ~650MB RSS under load | 35–83ms cold start (binary pre-cached) | Static crawl hit rate (88%) already clears the masterplan's 80% bar — see Crawl section |

## Search bake-off

Exa is the only provider in v1 (D1/D2 already resolved this — see [README](execution_phases/README.md#deviations-from-the-masterplan)); this measures headroom, not a vendor choice.

| Provider | Thin-query recall (N=4) | Mainstream recall (N=4) | p95 latency | $/query | Verdict |
|---|---|---|---|---|---|
| Exa | 4/4 returned genuinely on-topic, named competitors (no fixed ground-truth list exists for niche queries by definition — see sample titles below) | 3/4 (missed Asana/Trello/Monday/ClickUp for `"project management tool"`; surfaced Basecamp/ProjectManager/OpenProject instead) | ~740ms worst observed | $0.007 flat across `neural`/`keyword`/`auto` modes; `summary` content adds +$0.01 | **GO** |

Thin-query samples (queried cold, no seeding): `"WhatsApp first CRM for Indian SMBs"` → FloCRM, Dolphin CRM, Tanvik (all genuine, on-topic, India-specific WhatsApp CRM products). `"MEV monitoring for solo validators"` → MEV-Boost docs, SimplyStaking's block-proposal-monitor repo. Both qualitatively strong — Exa's neural search does what the masterplan needed it for.

Mid-difficulty recall: 3/4 (missed a fixed competitor list for `"customer feedback widget for SaaS"`, but the actual top hits — Mapster, Produktly, BetterFeedback — are legitimate, just not the specific names guessed in advance).

**Cost model derived:** $0.007/query × ~8 queries/run ≈ **$0.056/run** for search → **≈179 runs/month** against the $10/mo allowance. Comfortable headroom versus the masterplan's intended volume. A 10-request burst produced zero 429s (11.49s wall clock, fully sequential — no observed rate limiting at this volume).

## Crawl

- **Static (httpx + trafilatura) hit rate: 88% (35/40)** of real, currently-live pricing pages — see `tests/fixtures/pages/manifest.json` and `spikes/pricing_corpus.py` for the corpus.
- **Path-guess (`/pricing`, `/plans`, `/pricing-plans`) hit rate: 82% (33/40)**. The two honest misses are both multi-product enterprise vendors (Atlassian, Salesforce) whose real pricing lives off all three candidate paths — not a contrived example, a genuine limitation of path-guessing for enterprise SaaS.
- **Playwright recovery on the 5 static failures: 20% (1/5)**. It fixed `hubspot.com` — a genuine client-side-rendered gap (raw HTML had 0 extractable characters; the rendered DOM had a full page, once the price regex was widened for currency — see gotcha below). The other 4 remained blocked: 3 by Cloudflare's "Just a moment..." interstitial (`gitlab.com`, `bigcommerce.com`, `make.com` — a real bot-detection wall that a stock headless browser does not get past, and this project does not build evasion techniques to force it), and 1 (`canva.com`) that started 403-ing after repeated requests during this spike, likely our own crawl volume tripping its bot detection.
- **Cost of Playwright**: ~300–400MB image size delta (Chromium binary alone; headless-shell variant is ~262MB, full Chromium ~389MB), ~650MB RSS across all Chrome-related processes while a page is loaded, 35–83ms cold start once the binary is already present in the image.
- **Decision: Playwright deferred behind a feature flag, not built.** Static hit rate (88%) clears the masterplan §12.10 threshold (≥80%) with room to spare, and the failures that remain are either enterprise path-guessing misses (unrelated to JS rendering) or anti-bot walls Playwright doesn't solve anyway. Per the phase doc's own decision rule, it ships only if Phase 14 benchmark recall turns out to be JS-rendering-limited. **Masterplan §14 open item #3 is closed: no.**
- **Gotcha found and fixed on the spot**: the initial price-detection regex matched `$` only. `hubspot.com/pricing` served India-localized pricing in ₹ to this environment's egress IP, and was miscounted as a static failure until the regex was broadened to `[$€£¥₹]` + currency codes. Real vendors localize by request geography — any production price-detection logic needs the same fix.

## Fetch & path-guessing (Phase 03)

Measured for real against `api.retrieval.guess_path` (the shipped implementation, not a spike
approximation) with the phase doc's 7-candidate `PRICING_PATHS` list, over the same 40-domain
corpus Phase 01 used — see `spikes/pathguess_hitrate.py`.

- **Path-guess hit rate: 75% (30/40)**, using `guess_path`'s real success criterion: HTTP 200 *and*
  `looks_price_shaped()` matches the page's own **extracted, normalised** text (not raw HTML).
  Below Phase 01's 82% (33/40) baseline, and below the masterplan's 80% bar. Not a regression in
  the crawl mechanics — a difference in what's being measured, and a genuine, actionable finding
  in its own right (see below).
- **Why it's lower than Phase 01's number, specifically:** Phase 01's `spikes/crawl_static.py`
  counted a page as a hit if the price regex matched *either* the trafilatura-extracted text *or*
  the raw HTML body (`has_price(extracted) or has_price(text)`). Matching raw HTML catches prices
  sitting in `<script>` JSON blobs, structured-data tags, or other markup trafilatura correctly
  excludes as boilerplate. `guess_path` deliberately matches only the extracted, normalised text —
  it has to, because that's the same text `sources.extracted_text` stores and claim extraction
  (Phase 06) binds spans against (masterplan §4.8's byte-identical-text guarantee). Matching raw
  HTML here would make the routing heuristic and the stored content disagree about what "found
  the price" means.
- **Four newly-visible misses, root-caused individually** (`vercel.com`, `zoom.us`, `stripe.com`,
  `sendgrid.com` — all Phase 01 hits):
  - `vercel.com`: 200 OK, 39KB of raw HTML, but `trafilatura.extract(favor_precision=True)` returns
    **zero characters** — a JS-rendered SPA shell with no server-rendered price content, exactly
    the failure mode Phase 01's crawl-viability spike already named as a Playwright-relevant gap.
  - `zoom.us`: extracts to 557 characters of plan-tier *names* (`Basic`, `Meetings`, `AI Companion`)
    with no adjacent price digits — the numbers themselves are client-rendered.
  - `stripe.com`: extracts 2,872 real characters of pricing prose, but it's percentage- and
    cents-based (`2.9% + 30¢` style), which `PRICE_TOKEN_RE`'s currency-symbol set
    (`[$€£¥₹]`) doesn't match — a real gap in the loose price-token heuristic for usage-based
    pricing models, distinct from a crawl failure.
  - `sendgrid.com`: the `/pricing` page's actual current content (checked live) is a redirect
    notice — *"SendGrid ❤️ Twilio. We're finally moving in together on Twilio.com!"* — genuinely no
    pricing information there any more, not an extraction bug.
  - The remaining misses (`hubspot.com`, `gitlab.com`, `canva.com`, `bigcommerce.com`, `make.com`,
    `atlassian.com`) reproduce Phase 01's own already-documented misses (anti-bot walls, geo-priced
    content, or genuine off-path enterprise pricing).
- **Consequence, quantified per the phase doc's own risk table:** at 75% instead of the assumed
  80%, roughly 1 in 4 competitor pricing lookups falls through to a real search query instead of a
  direct fetch — not the ~4× blowup a total path-guessing failure would cause, since 3 of every 4
  lookups still avoid search entirely. Still comfortably inside Exa's ~179 runs/month ceiling
  (Search bake-off, above) even in the worst case. **Not a blocker; a real, measured number to
  carry into Phase 14's quota math instead of the Phase 01 estimate.**
- **Not changed in response to this number:** `PRICE_TOKEN_RE` and `favor_precision=True` are the
  phase doc's own specified design (byte-identical extracted text takes priority over maximising
  path-guess recall). Loosening either to chase a higher hit-rate number would be measuring the
  wrong thing — see `docs/execution_phases/phase-03-fetch-source-cache.md`'s own extraction design
  section.

## LLM

- **Model available**: `deepseek/deepseek-v4-flash`, exactly as the masterplan names it. Priced at **$0.0882/M in, $0.1764/M out, $0.0176/M cache read** (a genuine ~5× cache-read discount, confirmed against actual billed cost when a cache hit occurred).
- **Schema violation rate: 0/50 without provider pinning, 0/50 with `provider.require_parameters: true`.** Both clean on this model — no observed benefit from pinning at this scale, though pinning is still cheap insurance and costs nothing to keep.
- **Prompt cache hit confirmed: yes, but unreliable.** Firing an identical static-prefix request 10 times in a row hit the cache on only **1 of 10 calls (10%)** — OpenRouter's default routing is not sticky to a single backend node, so DeepSeek's server-side prefix cache mostly misses across calls even with byte-identical prefixes. When it did hit, the measured saving matched the pricing ratio (~5×) exactly. **This is a real deviation from the masterplan's assumed reliable ~4× extraction saving** — the saving is real when it lands, but is not something the cost model should count on happening consistently without further work (e.g. provider pinning to a single upstream, not just parameter-pinning).
- **Measured cost for a 60-page run: $0.012** (20 real pages cost $0.003998 total → $0.0002/page → ×60). Comfortably under the masterplan's $0.03/run estimate.
- **p50 / p95 per extraction call: 6,993ms / 14,665ms** with `response_format` schema enforcement (recommended path; also bounds output length to what the schema needs). **Without** schema enforcement (free-form JSON extraction), latency balloons and becomes far less predictable: p50 11,122ms / p95 **62,117ms**, with completion length ranging from 21 to 6,266 tokens for the same prompt shape. **Actionable finding for Phase 05/06: always use strict `response_format`/schema constraints for extraction calls — never free-form JSON** — it is cheaper, faster, and bounds the tail latency that the masterplan's "under three minutes" run promise depends on. Even with schema enforcement, a serial 60-page extraction pass at p50 ≈7s/call would take ~7 minutes — the executor must parallelize extraction calls, not run them sequentially, to stay inside the three-minute budget.

## Rate limits (measured)

| Source | Limit | Window | Auth | Reported via |
|---|---|---|---|---|
| GitHub REST (general) | 5,000 | per hour | PAT (Bearer) | `X-RateLimit-*` response headers |
| GitHub Search API | 30 | per minute | PAT (Bearer) | `X-RateLimit-*` response headers — **separate, much stricter limit than general REST**; the masterplan's `is:issue label:X sort:reactions-desc` query pattern must budget against this 30/min cap, not the 5,000/hr one |
| HN Algolia | none observed | — | none | no rate-limit headers present at all |
| Wayback CDX | none observed | — | none | no rate-limit headers; the real constraint is latency (~2–3s/call), not throttling |
| Stack Exchange | 300 | per day | none (anonymous) | **response body** (`quota_max`/`quota_remaining`), not headers — contradicts the phase doc's own stated assumption |
| npm downloads API | none observed | — | none | no rate-limit headers |
| Exa | none triggered at N=10 burst | — | API key | no 429 observed; no rate-limit headers surfaced either, so the practical ceiling is the $10/mo credit, not requests/sec |

## Credentials

| Service | Status | Obtained | Notes |
|---|---|---|---|
| Exa | ✅ Obtained | 2026-08-06 | Working, verified live |
| OpenRouter | ✅ Obtained | 2026-08-06 | Working, verified live; account has a $0.20/day spend cap configured — the full LLM validation in this phase cost well under a cent |
| GitHub | ✅ Obtained | 2026-08-06 | Fine-grained PAT, public-repo read-only. Sufficient for REST/GraphQL reads and issue search. **Starring endpoint is a permanent NO-GO** (restricted to repo admins/collaborators since 2026-06-30, fine-grained PATs unsupported for it) — see Go/No-Go table. Only total `stargazers_count` via `repo_metadata` is available; 90-day star velocity is out of reach for competitor repos. |
| Product Hunt | ✅ Obtained | 2026-08-07 | Developer token, verified live; `post_by_slug` cassette-recorded. No text-search field exists in v2 GraphQL — slug lookup only. |
| Reddit | ⏳ Not yet applied | — | 2–4 week manual approval once submitted. **Exit criterion "Reddit credentials applied for" is not met by this pass** — flagged here rather than silently marked done. Community mining ships on HN Algolia + GitHub + Stack Exchange regardless (D5). |
