# Phase 03 — Fetch, Text Extraction & Source Cache

| | |
|---|---|
| **Depends on** | [00](phase-00-foundation-contracts-ci.md), [01](phase-01-dependency-validation-spike.md) |
| **Unlocks** | [04](phase-04-search-domain-retrievers.md), [06](phase-06-claim-extraction-span-binding.md), [07](phase-07-entity-resolution.md) |
| **Milestone** | No |
| **Concrete output** | `fetch_source(url) -> Source` with URL canonicalisation, cache hit/miss accounting, and a **measured path-guessing hit rate** across the benchmark domains |

---

## Objective

Turn a URL into stored, deduplicated, plain-text content that extraction can bind spans against — and do it while making as few network requests as possible.

## Why this matters more than it looks

Two masterplan guarantees depend on this layer being exactly right:

1. **Span binding** (§4.8) requires that the text stored here is *byte-identical* to the text extraction sees. If normalisation happens twice, or differs between write and read, `source_text.find(quote)` fails and valid claims get silently dropped. This layer owns the canonical text, and nothing downstream may re-normalise it.
2. **Search cost** (§7) is dominated by *not searching*. Once a domain is known, its pricing is fetched at `/pricing`, not searched for. The path-guessing hit rate measured here directly determines per-run cost, and by extension every quota in [Phase 14](phase-14-benchmark-calibration.md).

---

## Scope

### In

- URL canonicalisation and deduplication
- HTTP fetching with `httpx`: timeouts, redirects, retries, conditional requests
- Text extraction with `trafilatura`
- Deterministic path guessing for known domains
- `robots.txt` compliance and polite rate limiting per host
- The source cache: 7-day TTL, content hashing, pinning
- TTL eviction that preserves drill-down

### Out

- Search ([Phase 04](phase-04-search-domain-retrievers.md)) — this layer takes URLs, it does not find them
- Structured API retrievers ([Phase 04](phase-04-search-domain-retrievers.md)) — GitHub and friends return JSON, not pages
- Claim extraction ([Phase 06](phase-06-claim-extraction-span-binding.md))
- Playwright, unless [Phase 01](phase-01-dependency-validation-spike.md) said it ships. If deferred, the interface accommodates it behind a flag and nothing else changes.

---

## Deliverables

```
src/api/retrieval/
├── __init__.py
├── canonical.py       # URL canonicalisation
├── fetch.py           # fetch_source, HTTP client config
├── extract_text.py    # trafilatura wrapper, normalisation
├── pathguess.py       # deterministic path candidates
├── robots.py          # robots.txt cache + politeness
├── cache.py           # source cache read/write, TTL eviction
└── errors.py          # typed failure classes
tests/
├── unit/test_canonical.py
├── unit/test_pathguess.py
├── unit/test_normalisation.py
├── integration/test_fetch.py
├── integration/test_cache.py
└── live/test_pathguess_hitrate.py
```

---

## Design

### URL canonicalisation

The cache key is `canonical_url`, unique in the schema. Canonicalisation must be **stable and idempotent**, or the cache silently stores duplicates and the "shared cache across users" cost saving (masterplan §8.2) evaporates.

Rules, applied in order:

1. Lowercase scheme and host; leave path case alone (paths are case-sensitive on many servers)
2. Force `https` where the host redirects to it; record the observed redirect rather than assuming
3. Strip the default port
4. Remove tracking params: `utm_*`, `fbclid`, `gclid`, `ref`, `source`, `mc_cid`, `mc_eid`
5. Sort remaining query params by key
6. Strip the fragment
7. Strip a trailing slash **except** on the bare root
8. Percent-encoding normalised to uppercase hex; decode unreserved characters

Rule 4 needs care: an allowlist would be safer but is unmaintainable, so a denylist is used and every removal is logged. If a page 404s after canonicalisation but the raw URL 200s, that is a canonicalisation bug and the log makes it findable.

**Property test:** `canon(canon(u)) == canon(u)` for all generated URLs. Idempotence is the invariant that matters; without it, cache keys drift.

### Path guessing — the cost lever

Given a registrable domain, generate ordered candidates and fetch until one yields a page containing a price-shaped token:

```python
PRICING_PATHS = ["/pricing", "/plans", "/pricing-plans", "/price",
                 "/pricing/", "/plans-and-pricing", "/subscribe"]
DOCS_PATHS    = ["/docs", "/documentation", "/developers", "/api"]
CHANGELOG     = ["/changelog", "/releases", "/whats-new", "/blog/changelog"]
```

Rules that keep this cheap and polite:

- **Stop at first success.** Ordered by observed real-world frequency (measured in [Phase 01](phase-01-dependency-validation-spike.md)).
- **Cap attempts per domain** (default 4). Seven candidate paths × N competitors is itself a request storm.
- **Cache negative results.** A domain with no `/pricing` should not be re-probed on the next run. Negative cache TTL is shorter (24h) than positive (7d) — sites add pricing pages.
- **Never guess into a subpath.** Only registrable-domain roots, to avoid probing an unrelated tenant on a shared host.

A `price-shaped token` is a regex over currency symbols, `/mo`, `/month`, `per seat`, `free`. Deliberately loose — this is a routing heuristic, not extraction. Extraction ([Phase 06](phase-06-claim-extraction-span-binding.md)) decides what the number means.

**Instrumented from the first commit:** every path-guess attempt records domain, path, outcome. The aggregate is the hit rate, reported in [Phase 14](phase-14-benchmark-calibration.md) and feeding the cost model.

### Fetching

`httpx.AsyncClient`, one shared instance with connection pooling.

| Setting | Value | Why |
|---|---|---|
| Connect timeout | 5s | Fail fast on dead hosts |
| Read timeout | 15s | Some pricing pages are slow |
| Total timeout | 20s | Bounds the task |
| Max redirects | 5 | Redirect loops exist |
| `User-Agent` | Descriptive, with contact URL | Politeness, and it materially reduces blocking |
| Max response size | 5 MB, streamed and truncated | A 200 MB PDF must not OOM the worker |

**Retry** reuses [Phase 02](phase-02-executor-core.md)'s policy — 429/5xx/timeout only, jittered backoff. `Retry-After` is honoured when present.

**Per-host politeness:** at most 2 concurrent requests and a minimum 250 ms gap per host, independent of the global crawl semaphore. Without this, path-guessing 7 candidates against one domain looks exactly like a scraper and gets the IP blocked.

**Conditional requests:** store `ETag` and `Last-Modified`; send `If-None-Match` / `If-Modified-Since` on refetch. A 304 refreshes the TTL without transferring or re-extracting — free, and meaningfully so for benchmark re-runs.

### robots.txt

Fetched once per host, cached 24h, parsed with `urllib.robotparser`. Disallowed URLs are not fetched; the attempt is recorded as a typed `RobotsDisallowed` failure so it surfaces as a coverage gap rather than a mysterious absence.

Aggregator sites with heavy anti-bot measures (G2, Capterra) are **never crawled at all** per masterplan §5 — they are read via SERP snippets in [Phase 04](phase-04-search-domain-retrievers.md). A hardcoded no-crawl set enforces this independently of what their robots.txt happens to say.

### Text extraction and normalisation

`trafilatura.extract()` with `include_comments=False`, `include_tables=True` (pricing tables matter), `favor_precision=True`.

Then exactly one normalisation pass, applied **once**, at write time:

1. Unicode NFC
2. `\r\n` and `\r` → `\n`
3. Collapse runs of 3+ blank lines to 2
4. Strip trailing whitespace per line
5. Ensure a single trailing newline

**The stored text is canonical and immutable.** Downstream code must never re-normalise — [Phase 06](phase-06-claim-extraction-span-binding.md) computes spans against exactly these bytes, and any second transformation invalidates every stored offset. This is enforced by a test asserting `normalise(normalise(t)) == normalise(t)` and by a comment in the extraction module pointing here.

Non-HTML content types (PDF, JSON, plain text) are routed to type-specific handlers or rejected with a typed error. A PDF pricing sheet is not worth supporting in v1; it is worth *failing clearly* on.

### The source cache

| Field | Purpose |
|---|---|
| `canonical_url` | Unique cache key |
| `content_hash` | SHA-256 of normalised text — keys the extraction cache in [Phase 06](phase-06-claim-extraction-span-binding.md) |
| `fetched_at`, `ttl_expires_at` | 7-day TTL per masterplan §9 |
| `http_status` | Non-200 outcomes cached too, so dead links are not re-fetched every run |
| `retrieval_reason` | Why this was fetched — debugging and coverage reporting |
| `is_pinned` | Benchmark sources, exempt from eviction ([Phase 00](phase-00-foundation-contracts-ci.md)) |

**Eviction and the 500 MB ceiling.** Supabase free is 500 MB, and `extracted_text` dominates it (~1.2 MB/run). Eviction nulls `extracted_text` on expired, unpinned rows while keeping the metadata row:

```sql
update sources
   set extracted_text = null
 where ttl_expires_at < now() and is_pinned = false and extracted_text is not null;
```

Drill-down still works for old reports because each claim carries its own `quote_context` window ([Phase 00](phase-00-foundation-contracts-ci.md)). The UI prefers full source text when present and falls back to the context window when not. **This fallback is tested here**, not assumed — a test evicts a source and asserts drill-down data is still reconstructable.

Cache statistics (`hit`, `miss`, `stale`, `conditional-304`) are counted per run and surface in `meta.cache_hit_rate` of the report contract.

---

## Testing

| Kind | Test | Asserts |
|---|---|---|
| Unit (property) | `canon(canon(u)) == canon(u)` over generated URLs | Idempotence — the cache-correctness invariant |
| Unit | Canonicalisation table: ~30 hand-written before/after pairs | Tracking params stripped, params sorted, fragment dropped, trailing slash rules, root preserved |
| Unit (property) | `normalise(normalise(t)) == normalise(t)` | Double-normalisation safety |
| Unit | Normalisation preserves character offsets for ASCII; documented behaviour for multi-byte | Span binding depends on this |
| Unit | Path-guess candidate ordering and attempt cap | No more than N requests per domain |
| Unit | Price-token regex: true positives (`$5/mo`, `€12 per seat`, `Free`) and true negatives (`5 stars`, `2024`) | Routing heuristic behaves |
| Integration | Fetch from cassettes: 200, 301→200, 404, 429-then-200, timeout-then-200, 5 MB truncation | Every HTTP path exercised offline |
| Integration | Cache miss → fetch → hit; second call makes zero network calls | Asserted by a mock transport that fails on any request |
| Integration | Conditional request: 304 refreshes TTL, does not re-extract | `content_hash` unchanged, no extraction invoked |
| Integration | Negative caching: a 404 is not re-fetched within TTL | Request count assertion |
| Integration | Eviction nulls text on expired unpinned rows only; pinned survive | Row-level assertions |
| Integration | **Drill-down survives eviction** — evict a source, reconstruct highlight from `quote_context` + `context_offset` | The 500 MB mitigation actually works |
| Integration | robots.txt disallow produces `RobotsDisallowed`, not a fetch | Compliance |
| Integration | Hardcoded no-crawl set is never fetched even if robots allows | G2/Capterra guard |
| Integration | Per-host politeness: 7 path guesses against one host are serialised with gaps | Timing assertion with a fake clock |
| Live (nightly) | Path-guess hit rate across the ~40 benchmark domains | The cost-model input. Fails loudly if it drops below the Phase 01 baseline. |

The extraction-quality corpus lives in `tests/fixtures/pages/`: raw HTML plus an expected-substring assertion (not exact-match, which would break on every trafilatura upgrade). ~15 pages spanning a clean marketing page, a pricing table, a JS-heavy SPA shell, a docs page, a changelog, and a cookie-wall.

---

## Exit criteria

- [ ] `fetch_source(url)` returns a stored `Source` with normalised text and content hash
- [ ] Canonicalisation idempotence property test passes
- [ ] Normalisation idempotence property test passes; the "normalise exactly once" rule documented at the module boundary
- [ ] Cache hit path proven to make **zero** network requests
- [ ] Conditional requests produce 304s and refresh TTL without re-extraction
- [ ] Eviction preserves drill-down via `quote_context` — proven by test
- [ ] robots.txt honoured; aggregator no-crawl set enforced independently
- [ ] Per-host politeness limits enforced
- [ ] **Path-guessing hit rate measured and written into `docs/external_apis.md`**
- [ ] Response size cap prevents OOM on a large body
- [ ] All HTTP tests run offline from cassettes
- [ ] Coverage ≥ 85% on `src/api/retrieval/`

---

## Risks

| Risk | Mitigation |
|---|---|
| Double-normalisation breaks span binding | Idempotence property test + single-owner rule + explicit module-boundary comment. This is the highest-consequence bug in the layer. |
| Path guessing reads as scraping and gets IPs blocked | Per-host concurrency and gap limits, descriptive UA with contact, attempt cap, negative caching. |
| Path-guess hit rate far below the 80% assumption | Measured in [Phase 01](phase-01-dependency-validation-spike.md), re-measured here. Consequence is ~4× search spend (~$0.03/run) — quantified, not fatal. |
| trafilatura upgrade changes extracted text | `content_hash` changes → extraction cache invalidates → claims re-extracted. Correct behaviour. Fixture tests assert substrings, not exact output, so they do not false-alarm. |
| Supabase 500 MB reached | Eviction from day one; benchmark pinning bounded (~12 MB); Phase 14 measures real bytes/run and re-derives headroom. |
| Cookie walls and consent interstitials yield junk text | Detected by a minimum-content heuristic (extracted text under ~200 chars with consent keywords) and recorded as a typed failure rather than fed to extraction as garbage. |

## Open decisions

1. **PDF support.** Rejected for v1 — fail with a typed error. Revisit only if [Phase 14](phase-14-benchmark-calibration.md) shows PDF pricing sheets costing measurable recall.
2. **Playwright placement.** If [Phase 01](phase-01-dependency-validation-spike.md) says it ships, does it live behind `fetch_source` as an automatic fallback on static-extraction failure, or as an explicit separate task kind the planner chooses? Automatic is simpler; explicit is cheaper and more debuggable. Lean explicit, decide with the Phase 01 numbers.
