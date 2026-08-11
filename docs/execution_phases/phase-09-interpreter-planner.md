# Phase 09 — Interpreter & Planner

| | |
|---|---|
| **Depends on** | [05](phase-05-llm-gateway.md) |
| **Unlocks** | [10](phase-10-task-handlers-e2e.md) |
| **Milestone** | No |
| **Concrete output** | `"AI expense tracker for freelancers"` → a typed `ResearchBrief` with per-field confidences, and a schema-validated task DAG with allocated budget |

---

## Objective

Stage 0 and Stage 1 of the masterplan's system flow: understand what was asked, then decide what research to do about it.

## What makes this planning rather than scripting

Masterplan §4.1 draws the line precisely:

> The planner does not write arbitrary steps. It selects from a fixed registry and allocates budget. […] It is still planning rather than scripting. The planner decides *whether* to mine Reddit, *how many* competitors to profile, *whether* GitHub is relevant to this category, *whether* funding matters. But the space of decisions is enumerable, validated, and replayable. **Free form planning is precisely where these systems become undebuggable.**

So the model makes genuine judgement calls — it just makes them by choosing from an enumerable space rather than emitting arbitrary code. A plan is a data structure that can be diffed, replayed, and asserted against.

---

## Scope

### In

- Stage 0: free text → `ResearchBrief` with per-field confidence
- The disambiguation decision — ask only when low confidence **and** plan-changing
- Stage 1: brief → validated task DAG with budget allocation
- Plan validation and repair
- Deterministic fallback plan

### Out

- Executing the plan ([Phase 02](phase-02-executor-core.md) runs it, [Phase 10](phase-10-task-handlers-e2e.md) implements handlers)
- The HTTP round-trip that surfaces disambiguation chips to a user ([Phase 12](phase-12-api-auth-quotas.md), [Phase 13](phase-13-frontend.md))
- Re-planning mid-run. Explicitly out of scope — masterplan §12.1 notes that the absence of a genuine re-planning loop is *why* LangGraph was rejected. Adding one silently would invalidate that decision.

---

## Deliverables

```
src/api/planner/
├── __init__.py
├── interpret.py       # Stage 0
├── plan.py            # Stage 1
├── registry.py        # TASKS registry with cost weights
├── validate.py        # DAG validation + repair
└── fallback.py        # deterministic default plan
src/api/prompts/interpret_brief.md
src/api/prompts/plan_dag.md
tests/unit/, tests/integration/
```

---

## Design

### Stage 0 — Interpret

Free text in, typed brief out:

```python
class ResearchBrief(BaseModel):
    category: str
    segment: str                # "B2B, freelancers and micro SMB"
    geography: str              # "global" | "india" | "us" | ...
    monetisation_guess: str
    keywords: list[str]
    field_confidence: dict[str, float]
```

`field_confidence` is **model-reported** here, and that is a deliberate and narrow exception to the "confidence is computed, never generated" rule (masterplan §12.5). The distinction matters: §12.5 governs *evidence* confidence, which is presented to the user as a quality signal and must be defensible. This is *interpretation* confidence — an internal routing signal deciding whether to ask a clarifying question. It never reaches the report's `confidence` fields. The two are separated by name and by type so they cannot be confused, and a test asserts brief confidences never flow into `findings.confidence`.

**Input validation happens before the model sees anything** (masterplan §8.3): 300-character cap, reject non-product queries, reject obvious injection attempts, blocklist harmful categories. Rejection is a typed error with a user-facing reason, not a silent empty run.

### The disambiguation decision

Masterplan §3: *low confidence AND plan changing → ask, else infer.* At most 2 chips, best guess pre-selected, user can ignore them and hit Go.

Both conditions are required, and the second is the interesting one. A field is **plan-changing** if a different value would produce a different DAG:

| Field | Plan-changing? | Why |
|---|---|---|
| `geography` | Yes | India-focused changes discovery queries and which sources matter |
| `segment` (B2B/B2C) | Yes | B2C skips GitHub; B2B may skip app stores |
| `category` | Yes | The whole plan |
| `monetisation_guess` | No | Affects synthesis framing, not which tasks run |

So low confidence on `monetisation_guess` never triggers a question — asking a user something that changes nothing is pure friction. The check is implemented as: hypothetically re-plan with the alternative value; if the DAG differs in node kinds or counts, it is plan-changing. This is computed, not asked of a model.

Cap at 2 chips even when 3 fields qualify, choosing by `(1 - confidence) × plan_delta_magnitude`.

### Stage 1 — Plan

The fixed registry from masterplan §4.1:

```python
TASKS = {
  "discover_competitors": {"args": ["query_variants"],       "cost_weight": 3},
  "profile_product":      {"args": ["entity_key"],           "cost_weight": 2},
  "extract_pricing":      {"args": ["entity_key"],           "cost_weight": 1},
  "mine_community":       {"args": ["keywords", "venues"],   "cost_weight": 4},
  "oss_profile":          {"args": ["repo"],                 "cost_weight": 1},
  "find_funding":         {"args": ["entity_key"],           "cost_weight": 1},
  "trend_signals":        {"args": ["keywords"],             "cost_weight": 2},
}
```

Output is a schema-validated DAG:

```json
{"nodes": [{"id": "t1", "kind": "discover_competitors",
            "args": {...}, "budget_weight": 3}],
 "edges": [["t1", "t2"]], "total_budget_weight": 40}
```

**The genuine decisions the planner makes:**

- Whether to run `mine_community` at all, and against which venues
- Whether GitHub is plausibly relevant — is this a category with OSS competitors?
- How many competitors to profile (bounded by `MAX_COMPETITORS_PROFILED`)
- Whether funding matters for this category
- Whether trend signals are worth the weight
- Budget allocation across branches

**What it cannot do:** invent a task kind, invent an argument, exceed the total budget, produce a cycle, or reference an undeclared node. All rejected by validation.

Note the DAG is a *seed*: `discover_competitors` does not know entity keys yet, so it emits `profile_product` children dynamically at runtime via `HandlerResult` ([Phase 02](phase-02-executor-core.md)). The planner allocates budget for that fan-out; the executor enforces it. This split is why the planner does not need to be re-invoked mid-run.

### Validation and repair

```
model output → schema parse → DAG validation
  ├─ valid       → use it
  ├─ repairable  → ONE repair attempt with the specific error
  └─ still bad   → deterministic fallback plan
```

Validation checks: every `kind` in the registry; args match the kind's declared model; no cycles; every edge references declared nodes; `total_budget_weight` equals the node sum and is within `RUN_BUDGET_WEIGHT`; at least one `discover_competitors` node exists (a plan with no discovery cannot produce anything).

The **fallback plan** is a hand-written, deterministic DAG covering the common case: discover → profile top N → extract pricing → mine community. A run never fails because planning failed. Its use is counted, because a rising fallback rate means the planning prompt is degrading, and that should be visible rather than masked by graceful degradation.

### Replayability

A plan is data, so it can be stored, diffed, and replayed. `runs.brief` holds the brief; the plan is reconstructible from the `tasks` table. This is the debugging property the masterplan is buying with the fixed registry: *"Debugging is a SQL query against a task table rather than a checkpoint blob"* (§12.1).

Tests store expected plans as fixtures and assert structural equality, so a prompt change that alters planning behaviour shows up as a diff rather than as mysterious downstream drift.

---

## Testing

| Kind | Test | Asserts |
|---|---|---|
| Unit | Input validation: over 300 chars, non-product query, injection attempt, blocklisted category | All rejected with typed reasons before any model call |
| Unit | DAG validation rejects: unknown kind, bad args, cycle, dangling edge, budget mismatch, over-budget, missing discovery | Each independently |
| Unit | Plan-changing classification per field | `monetisation_guess` never triggers a question |
| Unit | Chip cap at 2 even when 3 fields qualify; selection by ranking | Friction bounded |
| Unit | Fallback plan is itself valid | The safety net is safe |
| Unit | **Brief confidences never reach `findings.confidence`** | The §12.5 separation holds |
| Integration | Fixture queries → expected briefs, from committed LLM responses | Stage 0 deterministic offline |
| Integration | Fixture briefs → expected plans, structural equality | Stage 1 deterministic offline |
| Integration | Invalid plan → repair → valid | Repair works |
| Integration | Invalid twice → fallback used, counted | Never fails |
| Integration | **Category sensitivity** — a dev-tools query plans GitHub tasks; a consumer query does not | The planner actually decides, rather than emitting one template |
| Integration | Thin category ("MEV monitoring for solo validators") produces a plan, not an empty one | Degenerate input handled |
| Integration | Budget allocation sums correctly and respects the run cap | Arithmetic |

The category-sensitivity test is the one that proves this is a planner. If a dev-tools query and a consumer query produce identical DAGs, the model is not making the decisions masterplan §4.1 claims it makes, and the whole justification for having a planner collapses. It is worth asserting directly rather than assuming.

---

## Exit criteria

- [ ] Free text → `ResearchBrief` with per-field confidence
- [ ] Input validation runs **before** the model; all four rejection classes tested
- [ ] Disambiguation requires low confidence **and** plan-changing; at most 2 chips
- [ ] Plan-changing computed by hypothetical re-plan, not asked of a model
- [ ] Planner selects only from the fixed registry
- [ ] DAG validation rejects all seven invalid classes
- [ ] One repair attempt, then deterministic fallback; fallback rate counted
- [ ] Category-sensitivity test passes — dev-tools and consumer queries plan differently
- [ ] Brief confidence provably separated from evidence confidence
- [ ] Plans replayable from stored data
- [ ] Full suite offline from committed responses
- [ ] Coverage ≥ 85% on `src/api/planner/`

---

## Risks

| Risk | Mitigation |
|---|---|
| Planner emits the same plan regardless of category | Category-sensitivity test fails loudly. If it does fail, the prompt needs the category signal made more salient — this is exactly the feedback loop the test exists to provide. |
| Fallback used so often the planner is decorative | Fallback rate is a tracked metric surfaced in [Phase 14](phase-14-benchmark-calibration.md), not a silent degradation. |
| Disambiguation asks pointless questions | Plan-changing check is computed, not heuristic. A field that changes nothing cannot trigger a chip by construction. |
| Budget allocation consistently wrong | Executor enforces the real cap regardless ([Phase 02](phase-02-executor-core.md)). Planner allocation is advisory; the counter is authoritative. |
| Injection via the user query | Validated before the model; query is a bounded 300-char string, not page content. The serious injection surface is crawled pages, handled in [Phase 06](phase-06-claim-extraction-span-binding.md). |
| Someone adds a re-planning loop later | Would invalidate masterplan §12.1's rejection of LangGraph. If genuinely needed, that decision should be revisited explicitly in `docs/tracker.md`, not drifted into. |

## Open decisions

1. **Should the planner see [Phase 04](phase-04-search-domain-retrievers.md) budget state?** A run starting with an exhausted daily search budget should probably plan differently — more path-guessing, fewer discovery queries. Adds coupling. Proposal: pass remaining budget as a plan input in v1.1, not v1.
2. **Venue selection for `mine_community`.** With Reddit dropped ([D5](README.md#deviations-from-the-masterplan)), should the planner select venues, or should the handler pick from whatever is available? Proposal: planner expresses *intent* ("developer pain points"), handler maps intent to available venues. Closed in practice — the venue set is fixed (hn/github/stackexchange) and the planner selects them directly.
