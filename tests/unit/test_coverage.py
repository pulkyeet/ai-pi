"""Coverage scoring (Phase 08, masterplan Rule 4): weighted by
`cost_weight`, failed vs. budget-skipped vs. other-skipped kept distinct,
and Phase 07's `insufficient_signal` entities factored in.
"""

from __future__ import annotations

import pytest

from api.evidence.coverage import TaskOutcome, compute_coverage


def test_all_done_is_full_coverage() -> None:
    tasks = [
        TaskOutcome(kind="discover_competitors", cost_weight=3, status="done"),
        TaskOutcome(kind="profile_product", cost_weight=2, status="done"),
    ]
    result = compute_coverage(tasks)
    assert result.score == pytest.approx(1.0)
    assert result.failed_branches == ()
    assert result.budget_skipped_branches == ()


def test_weighted_by_cost_weight_not_task_count() -> None:
    tasks = [
        TaskOutcome(kind="mine_community", cost_weight=4, status="failed"),
        TaskOutcome(kind="extract_pricing", cost_weight=1, status="done"),
    ]
    result = compute_coverage(tasks)
    # 1 of 5 weight completed
    assert result.score == pytest.approx(0.2)
    assert result.failed_branches == ("mine_community",)


def test_a_dead_expensive_branch_costs_more_than_a_dead_cheap_one() -> None:
    heavy = compute_coverage(
        [
            TaskOutcome(kind="mine_community", cost_weight=4, status="failed"),
            TaskOutcome(kind="extract_pricing", cost_weight=1, status="done"),
        ]
    )
    light = compute_coverage(
        [
            TaskOutcome(kind="extract_pricing", cost_weight=1, status="failed"),
            TaskOutcome(kind="mine_community", cost_weight=4, status="done"),
        ]
    )
    assert heavy.score < light.score


def test_branch_with_at_least_one_done_task_is_not_dead() -> None:
    tasks = [
        TaskOutcome(kind="profile_product", cost_weight=2, status="done"),
        TaskOutcome(kind="profile_product", cost_weight=2, status="failed"),
    ]
    result = compute_coverage(tasks)
    assert result.failed_branches == ()
    assert result.score == pytest.approx(0.5)  # 2 of 4 planned weight completed


def test_failed_vs_budget_skipped_are_distinguished() -> None:
    tasks = [
        TaskOutcome(kind="find_funding", cost_weight=1, status="failed"),
        TaskOutcome(kind="trend_signals", cost_weight=2, status="skipped", skip_reason="budget"),
    ]
    result = compute_coverage(tasks)
    assert result.failed_branches == ("find_funding",)
    assert result.budget_skipped_branches == ("trend_signals",)


def test_skipped_for_a_non_budget_reason_is_its_own_category() -> None:
    tasks = [
        TaskOutcome(kind="oss_profile", cost_weight=1, status="skipped", skip_reason="unreachable")
    ]
    result = compute_coverage(tasks)
    assert result.other_skipped_branches == ("oss_profile",)
    assert result.failed_branches == ()
    assert result.budget_skipped_branches == ()


def test_a_kind_with_both_failed_and_budget_skipped_tasks_counts_as_failed() -> None:
    tasks = [
        TaskOutcome(kind="profile_product", cost_weight=2, status="failed"),
        TaskOutcome(kind="profile_product", cost_weight=2, status="skipped", skip_reason="budget"),
    ]
    result = compute_coverage(tasks)
    assert result.failed_branches == ("profile_product",)
    assert result.budget_skipped_branches == ()


def test_no_planned_tasks_is_vacuously_full_task_coverage() -> None:
    result = compute_coverage([])
    assert result.score == pytest.approx(1.0)


def test_insufficient_signal_entities_reduce_coverage() -> None:
    tasks = [TaskOutcome(kind="discover_competitors", cost_weight=3, status="done")]
    full = compute_coverage(tasks, total_entities=4, insufficient_signal_entities=0)
    reduced = compute_coverage(tasks, total_entities=4, insufficient_signal_entities=2)
    assert reduced.score < full.score
    assert reduced.score == pytest.approx(0.5)


def test_insufficient_signal_bounds_are_validated() -> None:
    with pytest.raises(ValueError):
        compute_coverage([], total_entities=2, insufficient_signal_entities=3)
    with pytest.raises(ValueError):
        compute_coverage([], total_entities=2, insufficient_signal_entities=-1)
