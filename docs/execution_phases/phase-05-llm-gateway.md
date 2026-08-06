# Phase 05 — LLM Gateway

| | |
|---|---|
| **Depends on** | [00](phase-00-foundation-contracts-ci.md), [01](phase-01-dependency-validation-spike.md) |
| **Unlocks** | [06](phase-06-claim-extraction-span-binding.md), [09](phase-09-interpreter-planner.md), [11](phase-11-synthesis-report-assembly.md) |
| **Milestone** | No |
| **Concrete output** | `llm.structured(schema, prompt_id, vars) -> T` — schema-guaranteed, cost-accounted, cache-optimised, replayable offline |

---

## Objective

One narrow interface for every model call in the system, such that no caller ever handles raw JSON, no call escapes cost accounting, and every call is replayable in CI without network or spend.

## Why a gateway rather than direct calls

Three masterplan requirements are only enforceable at a single chokepoint:

- **"Pydantic validation on every model response before anything touches the database. Never trust raw JSON"** (§6). One place to enforce it means it cannot be forgotten in the fourth call site.
- **Prompt caching** (§6) requires the static claim schema to be the *prefix* of every extraction prompt. That is a property of how prompts are assembled, so assembly belongs here.
- **Injection resistance** (§8.3) rests on page content only ever entering a schema-constrained prompt whose output space is the closed claim vocabulary. A gateway can enforce "untrusted content may only be passed as a constrained variable"; scattered call sites cannot.

Cost accounting is the fourth: `meta.cost_usd` in the report contract has to come from somewhere, and per-call attribution is impossible to reconstruct after the fact.

---

## Scope

### In

- OpenRouter client with provider pinning and structured output
- Versioned prompt files and deterministic assembly
- Pydantic validation with a bounded repair-retry
- Token and cost accounting per call, per task, per run
- Prompt-cache-optimal message ordering
- Response caching for deterministic replay
- Langfuse tracing

### Out

- Prompt *content* for extraction, planning, synthesis — those belong to the phases that own them ([06](phase-06-claim-extraction-span-binding.md), [09](phase-09-interpreter-planner.md), [11](phase-11-synthesis-report-assembly.md))
- Span binding ([Phase 06](phase-06-claim-extraction-span-binding.md))
- Any model routing ladder — masterplan §12.2 is explicit: single model, measure, only route upward if the benchmark shows a real gap

---

## Deliverables

```
src/api/llm/
├── __init__.py
├── client.py          # OpenRouter transport, retries, provider pinning
├── gateway.py         # structured() — the public interface
├── prompts.py         # prompt registry, loading, versioning, assembly
├── cost.py            # token + dollar accounting
├── cache.py           # response cache for replay
└── tracing.py         # Langfuse integration
src/api/prompts/
└── *.md               # versioned prompt files
tests/unit/, tests/integration/, tests/live/
```

---

## Design

### The public interface

```python
async def structured[T: BaseModel](
    schema: type[T],
    prompt_id: str,
    variables: Mapping[str, str],
    *,
    untrusted: Mapping[str, str] | None = None,
    ctx: LLMContext,
) -> LLMResult[T]: ...
```

Two things are deliberate.

**`untrusted` is a separate parameter.** Fetched page content goes here, never in `variables`. The gateway renders untrusted values into clearly delimited regions of the prompt and never lets them participate in template control flow. This makes the masterplan's §8.3 claim — "there is no path from page text to free text generation" — a property of the type signature rather than a convention. A reviewer can grep for `untrusted=` and see every place page content reaches a model.

**Return is `LLMResult[T]`, not `T`.** It carries the validated value plus `input_tokens`, `output_tokens`, `cached_tokens`, `cost_usd`, `latency_ms`, `provider`, `model`, `prompt_version`. Callers that ignore cost cannot accidentally drop it from accounting.

### Prompt management

Prompts are files, never inline strings (masterplan §6 and the `extractor_version` requirement from [Phase 00](phase-00-foundation-contracts-ci.md)):

```
src/api/prompts/
├── extract_claims.md
├── interpret_brief.md
├── plan_dag.md
├── synthesise_mvp.md
└── synthesise_risks.md
```

Each has YAML frontmatter declaring `id`, `schema` (the Pydantic model it produces), and `cache_prefix_ends_after` (which section is static). A registry loads them at import, hashes each, and exposes `prompt_version = f"{id}@{sha256[:8]}"`.

That version is what flows into `claims.extractor_version`, so editing a prompt automatically invalidates the extraction cache. No manual cache-busting, no stale claims produced by an old prompt masquerading as current.

**Assembly order is cache-optimal.** Per masterplan §6, structure every prompt so the static claim schema is the prefix:

```
[ system: role + closed claim vocabulary + output schema ]   ← static, cached
[ system: task instructions ]                                ← static, cached
--- cache breakpoint ---
[ user: variables ]                                          ← varies
[ user: <untrusted_content> … </untrusted_content> ]         ← varies, largest
```

The largest repeated segment sits before the breakpoint. Cache reads at ~$0.02/M against ~$0.09/M input is roughly a 4× saving on the bulk of every extraction call — which is the difference between the masterplan's $0.03/run and something several times higher.

A test asserts the assembled prefix is byte-identical across calls with different variables. Prefix drift silently destroys the cache-hit rate and shows up only as a cost regression weeks later, so it is checked mechanically.

### Structured output and provider pinning

Masterplan §6 warns that without `require_parameters: true`, a request can be routed to a backend that silently ignores `response_format`. [Phase 01](phase-01-dependency-validation-spike.md) measured the violation rate with and without pinning; this phase applies the pinning and asserts the measured behaviour.

```python
{
  "model": settings.llm_model,
  "response_format": {"type": "json_schema", "json_schema": {...}},
  "provider": {"require_parameters": True},
  "temperature": 0,
}
```

Temperature 0 everywhere — masterplan §11 requires deterministic CI replay.

### Validation and the repair retry

```
call → parse JSON → validate against Pydantic schema
  ├─ ok      → return LLMResult
  ├─ invalid → ONE repair retry with the validation error appended
  └─ invalid again → raise LLMValidationError (caller decides)
```

Exactly one repair attempt, deliberately. More retries on a model that cannot satisfy the schema is spend without progress, and the caller usually has a better fallback — for extraction, dropping the claim is *correct* behaviour, not a failure (masterplan rule: sentences that cannot be bound are dropped, not flagged).

Raw JSON never leaves this module. `LLMValidationError` carries the validation detail for logs, not the payload for downstream use.

### Cost accounting

Every call records tokens and dollars, attributed to `run_id` and `task_id`. Costs are computed from a config table of per-model rates rather than hardcoded, so a vendor reprice is a config edit.

Cache reads are priced separately from fresh input tokens — otherwise the prompt-caching optimisation is invisible in reporting and nobody notices when it breaks. `cache_hit_rate` on LLM calls is tracked alongside the source-cache rate.

Per-run totals flow into `runs.cost_usd` and the report's `meta.cost_usd`.

### Response caching

Keyed on `hash(prompt_version + model + rendered_messages)`. Two purposes:

1. **Deterministic CI.** Cached responses are committed for the benchmark corpus, so [Phase 14](phase-14-benchmark-calibration.md) replays run at zero cost and zero variance.
2. **Development loop.** Re-running a query while debugging downstream code does not re-spend.

Distinct from the extraction cache in [Phase 06](phase-06-claim-extraction-span-binding.md), which is keyed on `content_hash + extractor_version` and is permanent. This one is a transport-level cache; that one is a domain-level cache. Both exist; they are not interchangeable.

### Tracing

Langfuse wraps every call: prompt version, variables (untrusted content truncated), response, tokens, cost, latency. Free Hobby tier is 50,000 units/month — at ~65 units per run that is roughly **770 runs/month**, comfortably above the expected volume. Cost per run is visible per-trace, which is what makes a cost regression diagnosable rather than merely noticeable.

Tracing is fire-and-forget: a Langfuse outage must never fail a run.

---

## Testing

| Kind | Test | Asserts |
|---|---|---|
| Unit | Prompt registry loads all files; every `prompt_version` stable across processes | Version is a pure function of content |
| Unit | Editing a prompt file changes its version | Cache invalidation works |
| Unit | **Prefix stability** — assembled prefix byte-identical across differing variables | Prompt caching will actually hit |
| Unit | Untrusted content is delimited and cannot break out of its region — including inputs containing the delimiter itself | Injection resistance is structural |
| Unit | Untrusted content never participates in template control flow | §8.3 guarantee |
| Unit | Cost arithmetic across input / output / cached-read rates | `meta.cost_usd` is right |
| Unit | Cost config change alters computed cost with no code change | Reprice resilience |
| Integration | Valid response → validated model returned with populated `LLMResult` | Happy path |
| Integration | Malformed JSON → one repair retry → success | Repair works |
| Integration | Malformed twice → `LLMValidationError`; **no raw JSON escapes** | Fail-closed |
| Integration | Schema-violating response (valid JSON, wrong shape) → repair → error | Validation, not just parsing |
| Integration | 429 → backoff → success, reusing [Phase 02](phase-02-executor-core.md) retry policy | Transport resilience |
| Integration | Response cache: identical call twice makes one network request | Replay works |
| Integration | Langfuse unavailable → call still succeeds | Tracing is non-critical |
| Live (nightly) | 20 real extraction-shaped calls | Schema violation rate ≤ Phase 01 baseline |
| Live (nightly) | 10 identical-prefix calls | `cached_tokens > 0` — proves caching still active |

The prefix-stability and cached-tokens tests are the two that protect the cost model. Both failures are silent in normal operation.

---

## Exit criteria

- [ ] `structured()` is the only way any module calls a model — enforced by a lint rule banning direct client imports outside `src/api/llm/`
- [ ] Raw JSON never crosses the module boundary
- [ ] All prompts are versioned files; `prompt_version` derives from content hash
- [ ] Prefix stability test passes; caching confirmed hitting against the live nightly check
- [ ] Provider pinning applied; violation rate matches or beats [Phase 01](phase-01-dependency-validation-spike.md) measurement
- [ ] Exactly one repair retry; second failure raises typed
- [ ] Cost accounted per call, attributed to run and task, priced from config
- [ ] `untrusted` is a distinct parameter; no call site passes page content via `variables`
- [ ] Response cache enables full offline replay of the test suite
- [ ] Langfuse traces every call; outage does not fail a run
- [ ] Temperature 0 everywhere
- [ ] Coverage ≥ 85% on `src/api/llm/`

---

## Risks

| Risk | Mitigation |
|---|---|
| Prompt-cache prefix drifts, cost silently ~4× | Byte-identity unit test plus nightly live `cached_tokens > 0` assertion. Both must fail before cost regresses unnoticed. |
| Provider ignores `response_format` despite pinning | Measured in [Phase 01](phase-01-dependency-validation-spike.md); repair retry absorbs residual failures; nightly violation-rate test alarms on regression. |
| Model deprecated or repriced | Model ID and rates are config. Gateway boundary means a swap touches one module. Masterplan §12.2's "single model, measure first" stance is preserved. |
| Untrusted content escapes its region | Adversarial unit tests including delimiter-injection attempts. Structural containment, not filtering — consistent with masterplan §8.3. |
| Langfuse free tier exhausted | ~770 runs/month headroom against expected volume; sampling can be enabled if approached. Self-hosting is MIT-licensed and unmetered if it ever matters. |
| Repair retry masks a systematically bad prompt | Repair rate is a tracked metric, not just a fallback. A rising repair rate is a prompt-quality signal surfaced in [Phase 14](phase-14-benchmark-calibration.md). |

## Open decisions

1. **`extractor_version` composition.** [Phase 00](phase-00-foundation-contracts-ci.md) proposed `{prompt_hash}-{model_id}`. Confirm here, because it determines whether a model swap invalidates cached extractions. It should — the same page extracted by a different model is not the same claim provenance.
2. **Response-cache commit policy.** Committing benchmark LLM responses makes CI free and fully deterministic, at the cost of repo size. Estimate the bytes for 10 benchmark queries × ~60 pages before deciding; if too large, cache in CI artefacts keyed by content hash instead.
