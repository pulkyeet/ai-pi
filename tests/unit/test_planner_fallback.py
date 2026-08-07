from __future__ import annotations

from api.models.brief import ResearchBrief
from api.models.plan import TaskKind
from api.planner.fallback import fallback_plan
from api.planner.registry import DEFAULT_RUN_BUDGET_WEIGHT
from api.planner.validate import validate_plan_domain

BRIEF = ResearchBrief(
    category="expense management",
    segment="B2B, freelancers and micro SMB",
    geography="global",
    monetisation_guess="seat based SaaS",
    field_confidence={},
)


def test_fallback_plan_is_itself_valid_with_keywords() -> None:
    plan = fallback_plan(BRIEF, ["receipts", "mileage"])
    assert validate_plan_domain(plan, run_budget_weight=DEFAULT_RUN_BUDGET_WEIGHT) == []


def test_fallback_plan_is_itself_valid_without_keywords() -> None:
    plan = fallback_plan(BRIEF, [])
    assert validate_plan_domain(plan, run_budget_weight=DEFAULT_RUN_BUDGET_WEIGHT) == []


def test_fallback_always_includes_discovery() -> None:
    plan = fallback_plan(BRIEF, [])
    assert any(n.kind is TaskKind.DISCOVER_COMPETITORS for n in plan.nodes)


def test_fallback_skips_mine_community_without_keywords() -> None:
    plan = fallback_plan(BRIEF, [])
    assert not any(n.kind is TaskKind.MINE_COMMUNITY for n in plan.nodes)


def test_fallback_includes_mine_community_with_keywords() -> None:
    plan = fallback_plan(BRIEF, ["receipts"])
    mine_nodes = [n for n in plan.nodes if n.kind is TaskKind.MINE_COMMUNITY]
    assert len(mine_nodes) == 1
    assert mine_nodes[0].args["keywords"] == ["receipts"]


def test_fallback_budget_reserves_fanout_for_max_competitors() -> None:
    small = fallback_plan(BRIEF, [], max_competitors_profiled=1)
    large = fallback_plan(BRIEF, [], max_competitors_profiled=10)
    assert large.total_budget_weight > small.total_budget_weight


def test_fallback_query_variants_deduplicated_and_nonempty() -> None:
    plan = fallback_plan(BRIEF, ["expense management"])  # duplicates brief.category
    discover = next(n for n in plan.nodes if n.kind is TaskKind.DISCOVER_COMPETITORS)
    variants = discover.args["query_variants"]
    assert variants.count("expense management") == 1
    assert len(variants) == len(set(variants))
