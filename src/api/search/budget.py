"""Per-run retrieval budget (masterplan §7/§9, Phase 04 phase doc's own
pseudocode). A hard, in-memory call-count cap on search and fetch attempts
for one run — deliberately not the same thing as the credit allowance
tracked in `api.search.router`.

Scope note, mirroring `api.executor.budget.BudgetTracker` (Phase 02): this
object is owned by one run's caller (a future Phase 10 task handler), not
persisted or shared across processes. Exhaustion raises `BudgetExhausted`
synchronously so the caller can convert it into a coverage gap rather than a
crashed run — this phase builds and unit-tests the primitive; wiring
`spend_fetch()` into real `fetch_source` calls is Phase 10's job.
"""

from __future__ import annotations


class BudgetExhaustedError(Exception):
    """Named `BudgetExhausted` in the phase doc's own pseudocode; the
    `Error` suffix here follows this codebase's own convention (ruff N818,
    already applied to `RetryableError`/`FetchError` and friends)."""

    def __init__(self, resource: str, cap: int) -> None:
        super().__init__(f"retrieval budget exhausted: {resource} (cap={cap})")
        self.resource = resource
        self.cap = cap


class RetrievalBudget:
    def __init__(self, *, max_searches: int, max_fetches: int) -> None:
        self.max_searches = max_searches
        self.max_fetches = max_fetches
        self._searches_spent = 0
        self._fetches_spent = 0

    def spend_search(self, provider: str) -> None:
        if self._searches_spent >= self.max_searches:
            raise BudgetExhaustedError("searches", self.max_searches)
        self._searches_spent += 1

    def spend_fetch(self) -> None:
        if self._fetches_spent >= self.max_fetches:
            raise BudgetExhaustedError("fetches", self.max_fetches)
        self._fetches_spent += 1

    @property
    def searches_spent(self) -> int:
        return self._searches_spent

    @property
    def fetches_spent(self) -> int:
        return self._fetches_spent


__all__ = ["BudgetExhaustedError", "RetrievalBudget"]
