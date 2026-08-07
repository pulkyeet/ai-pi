"""Stage 1 — Plan (phase doc "Design > Stage 1" and "Validation and
repair"). Brief + keywords in, a schema-and-domain-valid seed `Plan` out.

```
model output -> schema parse -> DAG validation
  |- valid       -> use it
  |- repairable  -> ONE repair attempt with the specific error
  \\- still bad    -> deterministic fallback (api.planner.fallback), counted
```

**Why the model never sees `Plan` itself.** `PlanNode.args` is
`dict[str, Any]` (`api.models.plan`) — fine for a value that is only ever
constructed in Python, but `Any` has no JSON Schema representation that
survives OpenRouter's `strict: true` structured-output mode the way
`api.llm.gateway.structured()` requires. So the model targets `RawPlan`
below instead: every arg the registry can produce is its own concretely
typed, optional field (the same "every field optional, caller fills only
what its kind needs" shape already proven at 0/50 schema-violation rate for
`RawExtractedClaim`, Phase 01's spike). `_to_plan` then assembles each
node's `args` dict from whichever typed fields are non-`None`, and
constructs a real `Plan` — which is where `Plan`'s own invariants (cycle,
dangling edge, budget-weight mismatch, missing declared args) finally get
enforced, as an ordinary `pydantic.ValidationError`.

That conversion step is why "schema parse" and "DAG validation" collapse
into one repair round here rather than two: a `RawPlan` that parsed cleanly
can still fail to become a `Plan` (graph-invalid), and a `Plan` that
constructed cleanly can still fail `validate.validate_plan_domain` (seed
kind, over budget, missing discovery). Both failure kinds are described in
the same repair-note format and get exactly one combined retry, matching
the phase doc's diagram — not `structured()`'s own separate one-shot
JSON-schema repair (which still runs first, silently, for e.g. a
malformed `kind` string) plus a second repair on top of that.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from api.llm.gateway import LLMContext, LLMValidationError, structured
from api.models.brief import ResearchBrief
from api.models.plan import Plan, PlanNode, TaskKind
from api.planner.fallback import fallback_plan
from api.planner.registry import DEFAULT_MAX_COMPETITORS_PROFILED, DEFAULT_RUN_BUDGET_WEIGHT
from api.planner.validate import format_domain_errors, validate_plan_domain

PROMPT_ID = "plan_dag"


class RawPlanNode(BaseModel):
    id: str
    kind: TaskKind
    budget_weight: int
    query_variants: list[str] | None = None
    keywords: list[str] | None = None
    venues: list[str] | None = None
    max_profile_count: int | None = None
    consider_oss: bool | None = None
    consider_funding: bool | None = None


class RawPlan(BaseModel):
    nodes: list[RawPlanNode]
    edges: list[tuple[str, str]] = []
    total_budget_weight: int


@dataclass(frozen=True)
class PlanOutcome:
    plan: Plan
    used_fallback: bool
    repaired: bool


def _node_args(raw: RawPlanNode) -> dict[str, object]:
    fields = {
        "query_variants": raw.query_variants,
        "keywords": raw.keywords,
        "venues": raw.venues,
        "max_profile_count": raw.max_profile_count,
        "consider_oss": raw.consider_oss,
        "consider_funding": raw.consider_funding,
    }
    return {name: value for name, value in fields.items() if value is not None}


def _to_plan(raw: RawPlan) -> Plan:
    """May raise `pydantic.ValidationError` — a graph-invalid `RawPlan`
    (cycle, dangling edge, budget mismatch, or a node missing its kind's
    required arg) surfaces here, not inside `structured()`."""
    nodes = [
        PlanNode(id=n.id, kind=n.kind, args=_node_args(n), budget_weight=n.budget_weight)
        for n in raw.nodes
    ]
    return Plan(nodes=nodes, edges=raw.edges, total_budget_weight=raw.total_budget_weight)


def _variables(
    brief: ResearchBrief,
    keywords: list[str],
    *,
    run_budget_weight: int,
    max_competitors_profiled: int,
    repair_note: str = "",
) -> dict[str, str]:
    return {
        "category": brief.category,
        "segment": brief.segment,
        "geography": brief.geography,
        "keywords": ", ".join(keywords) if keywords else "(none stated)",
        "max_competitors_profiled": str(max_competitors_profiled),
        "run_budget_weight": str(run_budget_weight),
        "repair_note": repair_note,
    }


def _validation_error_detail(exc: ValidationError) -> str:
    parts = [
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
        for e in exc.errors(include_url=False, include_context=False, include_input=False)
    ]
    return "; ".join(parts) if parts else f"{exc.error_count()} validation error(s)"


async def _attempt(variables: dict[str, str], *, ctx: LLMContext, run_budget_weight: int) -> Plan:
    """Returns a valid `Plan`, or raises `ValueError` with a human-readable
    detail describing why (graph-invalid or domain-invalid) — the two
    failure kinds `plan_stage1` folds into one repair note."""
    result = await structured(RawPlan, PROMPT_ID, variables, ctx=ctx)
    try:
        plan = _to_plan(result.value)
    except ValidationError as exc:
        raise ValueError(_validation_error_detail(exc)) from exc

    domain_errors = validate_plan_domain(plan, run_budget_weight=run_budget_weight)
    if domain_errors:
        raise ValueError(format_domain_errors(domain_errors))

    return plan


async def plan_stage1(
    brief: ResearchBrief,
    keywords: list[str],
    *,
    ctx: LLMContext,
    run_budget_weight: int = DEFAULT_RUN_BUDGET_WEIGHT,
    max_competitors_profiled: int = DEFAULT_MAX_COMPETITORS_PROFILED,
) -> PlanOutcome:
    def to_fallback() -> PlanOutcome:
        return PlanOutcome(
            plan=fallback_plan(brief, keywords, max_competitors_profiled=max_competitors_profiled),
            used_fallback=True,
            repaired=False,
        )

    base_vars = _variables(
        brief,
        keywords,
        run_budget_weight=run_budget_weight,
        max_competitors_profiled=max_competitors_profiled,
    )

    try:
        plan = await _attempt(base_vars, ctx=ctx, run_budget_weight=run_budget_weight)
        return PlanOutcome(plan=plan, used_fallback=False, repaired=False)
    except LLMValidationError:
        pass
    except ValueError as exc:
        repair_vars = _variables(
            brief,
            keywords,
            run_budget_weight=run_budget_weight,
            max_competitors_profiled=max_competitors_profiled,
            repair_note=(
                f"Your previous plan was rejected for this reason: {exc}. "
                "Produce a corrected plan that fixes it."
            ),
        )
        try:
            plan = await _attempt(repair_vars, ctx=ctx, run_budget_weight=run_budget_weight)
            return PlanOutcome(plan=plan, used_fallback=False, repaired=True)
        except (LLMValidationError, ValueError):
            pass

    return to_fallback()


__all__ = ["PROMPT_ID", "PlanOutcome", "RawPlan", "RawPlanNode", "plan_stage1"]
