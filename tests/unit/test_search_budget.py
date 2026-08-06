"""RetrievalBudget is a hard per-run call-count cap (Phase 04 phase doc's
own pseudocode). The line that matters: exhaustion fires at exactly the
cap, never one call early."""

from __future__ import annotations

import pytest

from api.search.budget import BudgetExhaustedError, RetrievalBudget


def test_exhaustion_raises_at_exactly_the_cap() -> None:
    budget = RetrievalBudget(max_searches=3, max_fetches=2)
    budget.spend_search("exa")
    budget.spend_search("exa")
    budget.spend_search("exa")
    with pytest.raises(BudgetExhaustedError):
        budget.spend_search("exa")
    assert budget.searches_spent == 3


def test_never_exhausted_before_the_cap() -> None:
    budget = RetrievalBudget(max_searches=3, max_fetches=2)
    budget.spend_search("exa")
    budget.spend_search("exa")
    assert budget.searches_spent == 2


def test_search_and_fetch_caps_are_independent() -> None:
    budget = RetrievalBudget(max_searches=1, max_fetches=1)
    budget.spend_fetch()
    with pytest.raises(BudgetExhaustedError):
        budget.spend_fetch()
    budget.spend_search("exa")  # search cap untouched by the fetch exhaustion
    assert budget.searches_spent == 1
    assert budget.fetches_spent == 1


def test_budget_exhausted_error_carries_resource_and_cap() -> None:
    budget = RetrievalBudget(max_searches=0, max_fetches=5)
    with pytest.raises(BudgetExhaustedError) as exc_info:
        budget.spend_search("exa")
    assert exc_info.value.resource == "searches"
    assert exc_info.value.cap == 0
