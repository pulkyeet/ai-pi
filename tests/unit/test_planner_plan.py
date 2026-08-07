"""Unit-level coverage of `api.planner.plan`'s pure conversion step
(`_to_plan`, `RawPlan`/`RawPlanNode`) — no LLM, no Postgres. Exercises the
four invalid classes that `Plan`'s own pydantic invariants catch (unknown
kind, cycle, dangling edge, budget mismatch) *through this module's actual
construction path*, not just against `Plan` directly (already covered by
`tests/unit/test_contracts.py`, Phase 00's frozen suite) — so Phase 09's own
test suite demonstrates all seven classes from the phase doc's table, not
four of them by inheritance.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.models.plan import TaskKind
from api.planner.plan import RawPlan, RawPlanNode, _node_args, _to_plan


def _node(**overrides: object) -> RawPlanNode:
    defaults: dict[str, object] = dict(
        id="t1", kind=TaskKind.DISCOVER_COMPETITORS, budget_weight=3, query_variants=["x"]
    )
    defaults.update(overrides)
    return RawPlanNode(**defaults)  # type: ignore[arg-type]


def test_valid_raw_plan_converts() -> None:
    raw = RawPlan(nodes=[_node()], total_budget_weight=3)
    plan = _to_plan(raw)
    assert plan.nodes[0].kind is TaskKind.DISCOVER_COMPETITORS
    assert plan.nodes[0].args == {"query_variants": ["x"]}


def test_unknown_kind_rejected_at_schema_level() -> None:
    with pytest.raises(ValidationError):
        RawPlanNode(id="t1", kind="not_a_real_kind", budget_weight=1)  # type: ignore[arg-type]


def test_cycle_rejected() -> None:
    raw = RawPlan(
        nodes=[_node(id="t1"), _node(id="t2")],
        edges=[("t1", "t2"), ("t2", "t1")],
        total_budget_weight=6,
    )
    with pytest.raises(ValidationError):
        _to_plan(raw)


def test_dangling_edge_rejected() -> None:
    raw = RawPlan(nodes=[_node(id="t1")], edges=[("t1", "ghost")], total_budget_weight=3)
    with pytest.raises(ValidationError):
        _to_plan(raw)


def test_budget_mismatch_rejected() -> None:
    raw = RawPlan(nodes=[_node(id="t1", budget_weight=3)], total_budget_weight=99)
    with pytest.raises(ValidationError):
        _to_plan(raw)


def test_missing_required_arg_rejected() -> None:
    raw = RawPlan(nodes=[_node(query_variants=None)], total_budget_weight=3)
    with pytest.raises(ValidationError):
        _to_plan(raw)


def test_node_args_drops_unset_fields() -> None:
    node = _node(max_profile_count=5, consider_oss=None)
    args = _node_args(node)
    assert args == {"query_variants": ["x"], "max_profile_count": 5}


def test_node_args_includes_advisory_fields_when_set() -> None:
    node = _node(max_profile_count=5, consider_oss=True, consider_funding=False)
    args = _node_args(node)
    assert args["max_profile_count"] == 5
    assert args["consider_oss"] is True
    assert args["consider_funding"] is False
