from __future__ import annotations

from api.executor.budget import BudgetTracker


def test_admits_while_under_weight_cap() -> None:
    tracker = BudgetTracker(weight_cap=10)
    for _ in range(5):
        decision = tracker.try_reserve(2)
        assert decision.admit
    assert tracker.weight_spent == 10


def test_stops_dispatch_exactly_at_the_cap() -> None:
    tracker = BudgetTracker(weight_cap=5)
    assert tracker.try_reserve(3).admit
    assert tracker.try_reserve(2).admit
    decision = tracker.try_reserve(1)
    assert not decision.admit
    assert decision.reason == "budget_weight"
    assert tracker.weight_spent == 5  # rejected reservation is not counted


def test_charge_at_dispatch_holds_under_simulated_concurrency() -> None:
    """A burst of concurrent dispatches must each be checked against the
    running total as they're admitted, not against a stale pre-burst total —
    otherwise the cap is meaningless under exactly the concurrency it exists
    to bound. Simulated here as a tight sequential loop (this class has no
    internal concurrency of its own; the invariant it must hold is that
    admission accounts for every prior admission before deciding the next).
    """
    tracker = BudgetTracker(weight_cap=100)
    admitted = 0
    for _ in range(1000):
        if tracker.try_reserve(1).admit:
            admitted += 1
    assert admitted == 100
    assert tracker.weight_spent == 100


def test_skipped_tasks_are_not_charged() -> None:
    tracker = BudgetTracker(weight_cap=3)
    assert tracker.try_reserve(3).admit
    for _ in range(10):
        decision = tracker.try_reserve(1)
        assert not decision.admit
    assert tracker.weight_spent == 3


def test_usd_cap_independent_of_weight_cap() -> None:
    tracker = BudgetTracker(weight_cap=1000, usd_cap=1.0)
    assert tracker.try_reserve(1).admit
    tracker.record_spend_usd(1.5)
    decision = tracker.try_reserve(1)
    assert not decision.admit
    assert decision.reason == "budget_usd"


def test_no_usd_cap_means_unlimited_dollars() -> None:
    tracker = BudgetTracker(weight_cap=1000, usd_cap=None)
    tracker.record_spend_usd(1_000_000.0)
    assert tracker.try_reserve(1).admit
