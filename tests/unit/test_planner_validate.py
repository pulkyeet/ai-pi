from __future__ import annotations

from api.models.plan import Plan, PlanNode, TaskKind
from api.planner.validate import DomainValidationReason, validate_plan_domain

RUN_BUDGET = 40


def _discover(budget_weight: int = 3, **extra_args: object) -> PlanNode:
    return PlanNode(
        id="t1",
        kind=TaskKind.DISCOVER_COMPETITORS,
        args={"query_variants": ["x"], **extra_args},
        budget_weight=budget_weight,
    )


def test_valid_seed_plan_has_no_domain_errors() -> None:
    plan = Plan(nodes=[_discover()], total_budget_weight=3)
    assert validate_plan_domain(plan, run_budget_weight=RUN_BUDGET) == []


def test_runtime_only_kind_in_seed_plan_is_bad_args() -> None:
    node = PlanNode(
        id="t1", kind=TaskKind.PROFILE_PRODUCT, args={"entity_key": "web:x.com"}, budget_weight=2
    )
    plan = Plan(nodes=[node], total_budget_weight=2)
    errors = validate_plan_domain(plan, run_budget_weight=RUN_BUDGET)
    assert [e.reason for e in errors] == [
        DomainValidationReason.BAD_ARGS,
        DomainValidationReason.MISSING_DISCOVERY,
    ]


def test_over_budget_plan_flagged() -> None:
    plan = Plan(nodes=[_discover(budget_weight=100)], total_budget_weight=100)
    errors = validate_plan_domain(plan, run_budget_weight=RUN_BUDGET)
    assert [e.reason for e in errors] == [DomainValidationReason.OVER_BUDGET]


def test_missing_discovery_node_flagged() -> None:
    node = PlanNode(
        id="t1",
        kind=TaskKind.MINE_COMMUNITY,
        args={"keywords": ["x"], "venues": ["hn"]},
        budget_weight=4,
    )
    plan = Plan(nodes=[node], total_budget_weight=4)
    errors = validate_plan_domain(plan, run_budget_weight=RUN_BUDGET)
    assert [e.reason for e in errors] == [DomainValidationReason.MISSING_DISCOVERY]


def test_multiple_independent_errors_all_reported_together() -> None:
    node = PlanNode(
        id="t1", kind=TaskKind.FIND_FUNDING, args={"entity_key": "web:x.com"}, budget_weight=100
    )
    plan = Plan(nodes=[node], total_budget_weight=100)
    errors = validate_plan_domain(plan, run_budget_weight=RUN_BUDGET)
    reasons = {e.reason for e in errors}
    assert reasons == {
        DomainValidationReason.BAD_ARGS,
        DomainValidationReason.OVER_BUDGET,
        DomainValidationReason.MISSING_DISCOVERY,
    }
