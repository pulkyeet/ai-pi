"""Cache key stability: same query + provider + params -> same key, param
order irrelevant. If this drifts, a second identical query never hits the
cache and every run pays full price (masterplan §9)."""

from __future__ import annotations

from api.search.cache import cache_key


def test_stable_for_identical_inputs() -> None:
    k1 = cache_key("project management tool", "exa", {"limit": 10, "site": None})
    k2 = cache_key("project management tool", "exa", {"limit": 10, "site": None})
    assert k1 == k2


def test_param_order_is_irrelevant() -> None:
    k1 = cache_key("q", "exa", {"limit": 10, "site": "g2.com"})
    k2 = cache_key("q", "exa", {"site": "g2.com", "limit": 10})
    assert k1 == k2


def test_differs_by_query() -> None:
    assert cache_key("q1", "exa", {}) != cache_key("q2", "exa", {})


def test_differs_by_provider() -> None:
    assert cache_key("q", "exa", {}) != cache_key("q", "other-provider", {})


def test_differs_by_params() -> None:
    assert cache_key("q", "exa", {"limit": 10}) != cache_key("q", "exa", {"limit": 5})
