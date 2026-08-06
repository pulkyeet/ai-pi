# Phase 07 — Entity Resolution & Identity

| | |
|---|---|
| **Depends on** | [00](phase-00-foundation-contracts-ci.md), [03](phase-03-fetch-source-cache.md) |
| **Unlocks** | [08](phase-08-grading-confidence-contradictions.md), [10](phase-10-task-handlers-e2e.md) |
| **Milestone** | No |
| **Concrete output** | `resolve_entity(evidence) -> Entity` producing canonical keys, merged aliases, and a derived maturity tier — with the `.fly.dev` collapse case proven impossible |

---

## Objective

Decide what counts as one product, give it a stable key, merge the aliases under which it arrives, and classify how real it is.

## Why this is load-bearing

Two masterplan guarantees depend on it:

**Rule 2** (§2): *an entity is only listed if it has a verifiable public artifact* — a 200-responding URL, an existing repo, a real package, a live store listing. This eliminates hallucinated competitors **structurally rather than by prompting**. That check lives here.

**Maturity tiers** (§4.5) matter more than they look: *"Three funded competitors plus forty weekend projects" is a completely different answer for a founder than "forty three competitors"* — and once `.fly.dev` entities are accepted, the second is what a naive system prints.

---

## Scope

### In

- Entity key derivation for every scheme, with PSL private-domain handling
- Artifact verification — no artifact, no entity
- Alias detection and merging
- Maturity tier derivation
- Entity persistence with idempotent upsert

### Out

- Discovering candidates ([Phase 10](phase-10-task-handlers-e2e.md))
- Claim attribution to entities beyond the resolution step ([Phase 08](phase-08-grading-confidence-contradictions.md) consumes resolved entities)
- Near-duplicate *complaint* detection via pgvector — that is text similarity, not entity identity, and belongs to [Phase 11](phase-11-synthesis-report-assembly.md)

---

## Deliverables

```
src/api/resolve/
├── __init__.py
├── entity_key.py      # derivation per scheme, PSL handling
├── verify.py          # artifact verification
├── alias.py           # merge detection and execution
├── maturity.py        # tier derivation
└── store.py           # idempotent upsert
tests/unit/, tests/integration/
```

---

## Design

### Entity keys

Masterplan §4.5's scheme-prefixed keys, because plenty of real products have no domain of their own:

```
web:expensify.com
web:ai-expense-reporter.fly.dev
gh:owner/repo
npm:pkg   |  pypi:pkg
chrome:<ext-id>  |  ios:<app-id>  |  hf:user/space
ph:<slug>          # pre-launch, Product Hunt only
```

**The PSL flag is the whole trick for `web:` keys:**

```python
extract = tldextract.TLDExtract(include_psl_private_domains=True)
extract("ai-expense-reporter.fly.dev").registered_domain
# -> "ai-expense-reporter.fly.dev"   not "fly.dev"
```

The Public Suffix List already encodes which hosts are multi-tenant. Without `include_psl_private_domains=True` — which is **not** the default, and the masterplan notes it has bitten people — every Fly-hosted product collapses into one entity called `fly.dev`, and likewise for `vercel.app`, `netlify.app`, `github.io`, `herokuapp.com`, `pages.dev`, `railway.app`, `onrender.com`, `web.app`, `firebaseapp.com`, `workers.dev`.

This is tested exhaustively — one assertion per PaaS host — because it is a single constructor flag standing between correct behaviour and a report that says "43 competitors" when it means "3 competitors and 40 weekend projects".

Other schemes normalise per their own rules: `gh:` lowercases owner and repo and strips `.git`; `npm:` respects scoped-package case rules; `pypi:` applies PEP 503 normalisation (lowercase, runs of `-_.` collapse to `-`).

### Artifact verification — enforcing Rule 2

No entity is created without a verified public artifact:

| Scheme | Verification | Grade of evidence |
|---|---|---|
| `web:` | HEAD/GET returns 200 (via [Phase 03](phase-03-fetch-source-cache.md), cached) | A |
| `gh:` | GitHub API returns the repo, not 404 | A |
| `npm:` / `pypi:` | Registry API returns the package | A |
| `chrome:` / `ios:` | Store listing resolves | A |
| `hf:` | HF API returns the model or space | A |
| `ph:` | Product Hunt API returns the post | B — pre-launch, no independent artifact |

Verification failure means the candidate is **discarded and counted**, not listed with low confidence. The masterplan is explicit that this is structural: a hallucinated competitor has no URL that 200s, so it cannot survive.

Results are cached — verification is a fetch, and re-verifying the same domain across runs would waste the [Phase 04](phase-04-search-domain-retrievers.md) budget.

A soft-404 guard is needed: many parked domains return 200 with a placeholder page. A minimum-content heuristic plus parking-page keyword detection catches the common cases. Imperfect, and the residual is handled by maturity tiering rather than pretended away.

### Alias merging

The same product arrives under different keys. Masterplan §4.5 names two merge triggers, and a third is worth adding:

1. **Repo `homepage` points at a domain** → `gh:owner/repo` ≡ `web:domain`
2. **Site footer links back to a repo** → same edge, other direction
3. **Package `repository` field points at a repo** → `npm:pkg` ≡ `gh:owner/repo`

Merging is directional: the **canonical** key wins, chosen by scheme precedence `web > gh > npm/pypi > chrome/ios/hf > ph`. A product with a real domain is keyed by its domain; a repo-only project is keyed by its repo. Merged keys land in `entity_aliases`, so a later arrival under an alias resolves to the canonical entity rather than creating a duplicate.

Merges must be **idempotent and order-independent**: resolving A then B must produce the same result as B then A. This is a property test, because entity arrival order depends on task scheduling and is effectively nondeterministic.

Merges are never undone within a run. A bad merge is a data-quality issue, but an unmergeable oscillation is a correctness bug — so merges only ever collapse, never split.

### Maturity tiers

Masterplan §4.5: `established | funded | indie | hobby | abandoned`.

Derived deterministically from available signals, never asked of a model:

| Signal | Source |
|---|---|
| Domain age | WHOIS-free heuristic: earliest Wayback snapshot ([Phase 04](phase-04-search-domain-retrievers.md)) |
| Last commit | GitHub API |
| Star count and 90-day velocity | GitHub API |
| Funding claims | `company.funding_total_usd` claims ([Phase 06](phase-06-claim-extraction-span-binding.md)) |
| Store install counts | Chrome/App Store listings |
| Package download counts | npm/PyPI |
| Host type | PaaS subdomain vs own registrable domain |

Rules are an ordered decision list, evaluated first-match, so classification is explainable — the report can state *why* something is `hobby`:

```
abandoned  ← last commit > 18 months AND no other liveness signal
funded     ← any A/B-grade funding claim OR company.stage in {seed, series-*}
established← domain age > 3y AND (installs > 100k OR downloads > 100k/mo OR stars > 5k)
hobby      ← PaaS subdomain OR (stars < 100 AND downloads < 1k/mo)
indie      ← otherwise
```

Every tier assignment records the rule that fired and the evidence that satisfied it. Unknown-signal cases default to `indie` with a recorded `insufficient_signal` flag rather than guessing — and that flag feeds `coverage`, since a run where most entities are unclassifiable is a run whose maturity story should be caveated.

Thresholds are first-pass guesses and are explicitly tunable in [Phase 14](phase-14-benchmark-calibration.md) against the benchmark set.

### Persistence

Upsert on `entity_key`, `ON CONFLICT DO UPDATE` for metadata refresh. Safe to call concurrently from multiple task handlers — which matters, because [Phase 10](phase-10-task-handlers-e2e.md) resolves entities from several tasks running in parallel, and two tasks discovering the same competitor simultaneously is the common case, not the edge case.

---

## Testing

| Kind | Test | Asserts |
|---|---|---|
| Unit | **PSL table** — one assertion per PaaS host (`fly.dev`, `vercel.app`, `netlify.app`, `github.io`, `herokuapp.com`, `pages.dev`, `railway.app`, `onrender.com`, `workers.dev`, `web.app`) | Each yields a distinct key; none collapses to the bare PaaS domain |
| Unit | `web:` derivation strips `www.`, lowercases host, ignores path and query | Canonical form |
| Unit | `gh:` lowercases, strips `.git`, handles trailing slash and full URLs | Canonical form |
| Unit | `pypi:` applies PEP 503 normalisation (`Foo.Bar_Baz` → `foo-bar-baz`) | Registry semantics |
| Unit (property) | `EntityKey.parse(str(k)) == k` across all schemes | Round-trip |
| Unit | Scheme precedence ordering for canonical selection | Deterministic winner |
| Unit (property) | **Merge order independence** — resolving a candidate set in any permutation yields the same entity graph | Scheduling nondeterminism is safe |
| Unit | Maturity decision list: one case per rule, plus boundary values on each threshold | Explainable classification |
| Unit | Insufficient signal → `indie` + flag, never a guess | Honest defaults |
| Integration | Artifact verification: 200 → created; 404 → discarded and counted; parked page → discarded | Rule 2 enforced |
| Integration | Verification results cached; second resolution makes zero network calls | Budget respected |
| Integration | Alias merge via repo `homepage`; via footer backlink; via package `repository` | All three triggers |
| Integration | Arriving under an alias resolves to the canonical entity, creates no duplicate | Merge is effective |
| Integration | Concurrent upsert of the same key from two tasks → one row, no error | Real concurrency pattern |
| Integration | **The `.fly.dev` scenario end-to-end** — 5 distinct Fly-hosted products resolve to 5 entities, all tiered `hobby` | The masterplan's motivating example |

That last test is the phase's signature test. It is the exact failure the masterplan calls out, and it should exist as a named, readable test rather than being implied by the PSL unit tests.

---

## Exit criteria

- [ ] `include_psl_private_domains=True` set explicitly, with a comment explaining why
- [ ] PSL table test covers ≥ 10 PaaS hosts, all distinct
- [ ] The `.fly.dev` end-to-end scenario test passes
- [ ] Artifact verification enforced for every scheme; unverified candidates discarded and counted
- [ ] Parked/soft-404 guard implemented, with known limitations documented
- [ ] All three alias merge triggers implemented and tested
- [ ] Merge order-independence property test passes
- [ ] Maturity tiers derived by explainable rules; every assignment records its rule and evidence
- [ ] `insufficient_signal` flows into coverage rather than being silently defaulted
- [ ] Concurrent upsert safe
- [ ] Verification results cached
- [ ] Coverage ≥ 85% on `src/api/resolve/`

---

## Risks

| Risk | Mitigation |
|---|---|
| PSL flag forgotten in a refactor | Explicit table test with ≥ 10 hosts fails loudly. Comment at the construction site explains the consequence. |
| Alias merging is too aggressive, collapsing distinct products | Only the three evidence-based triggers; no name-similarity heuristics. A wrong merge needs a *false backlink*, which is rare. Order-independence test catches instability. |
| Alias merging too conservative, duplicating entities | Measured in [Phase 14](phase-14-benchmark-calibration.md) as a precision metric on the benchmark set. Additional triggers added only with evidence. |
| Maturity thresholds arbitrary | Explicitly first-pass. Tuned in [Phase 14](phase-14-benchmark-calibration.md) against hand-labelled benchmark entities. The rules are visible and adjustable, unlike a model's judgement. |
| Parked domains pass verification | Heuristic guard; residual handled by tiering (a parked domain has no other signals → `hobby` or `abandoned`). Not pretended solved. |
| Verification burns the fetch budget | Cached, and HEAD where the server supports it. Verification failures cached too, so dead candidates are not re-probed. |

## Open decisions

1. **Cross-run entity persistence.** Entities are global (`entity_key` is unique table-wide), so a second run in the same category reuses them — a real cost saving. But maturity signals go stale. Proposal: refresh maturity when the entity's newest signal is over 30 days old, otherwise reuse. Confirm once [Phase 14](phase-14-benchmark-calibration.md) shows how often categories overlap.
2. **Should `ph:` entities appear in reports at all?** A Product Hunt slug with no other artifact is a pre-launch announcement, not a shippable competitor. Proposal: include, tiered `hobby`, clearly labelled pre-launch — the founder probably does want to know someone announced this last week.
