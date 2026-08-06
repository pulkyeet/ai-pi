"""In-memory, per-dispatch-loop budget enforcement.

Scope note (see docs/tracker.md Phase 02 entry): this tracker is owned by one
`Executor.submit()` call's dispatch loop, matching masterplan §4.2's own
reference pseudocode (a plain Python counter inside one `asyncio.TaskGroup`).
It is correct for the default single-worker deployment. If a second worker
process is ever added against the *same run*, each has its own tracker and
the weight cap is enforced per-process, not globally — a known, documented
gap, not an oversight; promoting this to a Postgres-backed cross-worker cap
is deferred until a real multi-worker deployment needs it.

Budget is charged at dispatch, not completion: a burst of concurrent
dispatches must each pass the check before any of them completes, or the cap
is meaningless under exactly the concurrency it exists to bound.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetDecision:
    admit: bool
    reason: str | None = None  # "budget_weight" | "budget_usd", set iff not admit


class BudgetTracker:
    def __init__(self, weight_cap: int, usd_cap: float | None = None) -> None:
        self._weight_cap = weight_cap
        self._usd_cap = usd_cap
        self._weight_spent = 0
        self._usd_spent = 0.0

    def try_reserve(self, weight: int) -> BudgetDecision:
        if self._usd_cap is not None and self._usd_spent >= self._usd_cap:
            return BudgetDecision(admit=False, reason="budget_usd")
        if self._weight_spent + weight > self._weight_cap:
            return BudgetDecision(admit=False, reason="budget_weight")
        self._weight_spent += weight
        return BudgetDecision(admit=True)

    def record_spend_usd(self, amount: float) -> None:
        self._usd_spent += amount

    @property
    def weight_spent(self) -> int:
        return self._weight_spent

    @property
    def usd_spent(self) -> float:
        return self._usd_spent
