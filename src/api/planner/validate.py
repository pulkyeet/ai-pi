"""DAG domain validation (phase doc "Design > Validation and repair").

Takes an already-constructed `api.models.plan.Plan` — by the time one
exists, its own pydantic invariants have already passed: no duplicate node
ids, no cycle, no dangling edge, `total_budget_weight` matches the node
sum, and every node has its kind's declared args present (four of the
phase doc's seven invalid classes, plus "missing declared args"). Unknown
`kind` values never reach here at all — `TaskKind` is a closed `StrEnum`,
so `RawPlanNode.kind` (`api.planner.plan`) fails to parse before a `Plan`
can even be attempted. `api.planner.plan` is what turns model output into a
`Plan` (or a caught `ValidationError`) in the first place; this module is
deliberately construction-method-agnostic — it validates whatever `Plan` it
is handed, whether that came from the planning LLM, the deterministic
fallback, or a test fixture.

What is left, and what this module actually checks, are the three domain
rules a generic graph validator cannot know: **bad args** — specifically,
only a seed-restricted kind may appear at all (`api.planner.registry`);
`profile_product`/`extract_pricing`/`oss_profile`/`find_funding` all
require an `entity_key`/`repo` that cannot exist yet at planning time, and
`Plan` itself has no notion of "seedable" to reject them with. The
remaining per-arg-value type checks below are defense in depth, not the
primary gate — `api.planner.plan` already gets this for free from typed
`RawPlanNode` fields for output that actually came from the planning LLM.
**Over budget** (`Plan` only proves internal consistency; whether that
total fits the run's actual budget is an external comparison) and
**missing discovery** (a plan with no `discover_competitors` node cannot
produce anything — masterplan "planner selects from a fixed registry",
phase doc's own validation list) round out the three.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from api.models.plan import Plan, PlanNode, TaskKind
from api.planner.registry import SEED_KINDS, TASK_ARGS

_ADVISORY_ARG_TYPES: dict[str, type] = {
    "max_profile_count": int,
    "consider_oss": bool,
    "consider_funding": bool,
}


class DomainValidationReason(StrEnum):
    BAD_ARGS = "bad_args"
    OVER_BUDGET = "over_budget"
    MISSING_DISCOVERY = "missing_discovery"


@dataclass(frozen=True)
class DomainValidationError:
    reason: DomainValidationReason
    detail: str


def _node_arg_errors(node: PlanNode) -> list[str]:
    if node.kind not in SEED_KINDS:
        return [
            f"node {node.id!r}: kind {node.kind!r} cannot appear in a seed plan "
            "(it requires an entity_key/repo that isn't known until runtime)"
        ]

    errors: list[str] = []
    for arg_name in TASK_ARGS[node.kind]:
        value = node.args.get(arg_name)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            errors.append(f"node {node.id!r}: arg {arg_name!r} must be a list of strings")

    for arg_name, expected_type in _ADVISORY_ARG_TYPES.items():
        if arg_name in node.args and not isinstance(node.args[arg_name], expected_type):
            errors.append(
                f"node {node.id!r}: advisory arg {arg_name!r} must be {expected_type.__name__}"
            )

    return errors


def validate_plan_domain(plan: Plan, *, run_budget_weight: int) -> list[DomainValidationError]:
    """Independent checks, all evaluated (not short-circuited) so a repair
    prompt can name every problem at once rather than iterating one error
    per round trip."""
    errors: list[DomainValidationError] = []

    arg_errors = [e for node in plan.nodes for e in _node_arg_errors(node)]
    if arg_errors:
        errors.append(DomainValidationError(DomainValidationReason.BAD_ARGS, "; ".join(arg_errors)))

    if plan.total_budget_weight > run_budget_weight:
        errors.append(
            DomainValidationError(
                DomainValidationReason.OVER_BUDGET,
                f"total_budget_weight {plan.total_budget_weight} exceeds the run cap "
                f"{run_budget_weight}",
            )
        )

    if not any(n.kind is TaskKind.DISCOVER_COMPETITORS for n in plan.nodes):
        errors.append(
            DomainValidationError(
                DomainValidationReason.MISSING_DISCOVERY,
                "plan has no discover_competitors node — a run with no discovery step "
                "cannot produce anything",
            )
        )

    return errors


def format_domain_errors(errors: list[DomainValidationError]) -> str:
    return "; ".join(f"{e.reason.value}: {e.detail}" for e in errors)


__all__ = [
    "DomainValidationError",
    "DomainValidationReason",
    "format_domain_errors",
    "validate_plan_domain",
]
