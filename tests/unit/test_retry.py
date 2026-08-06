from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from api.executor.retry import (
    NonRetryableError,
    RetryableError,
    backoff_delay,
    is_retryable,
)

RETRYABLE_CODES = (429, 500, 502, 503, 504)
NON_RETRYABLE_CODES = (400, 401, 403, 404, 422)


def test_retryable_status_codes_classified_retryable() -> None:
    for code in RETRYABLE_CODES:
        assert is_retryable(RetryableError("x", code=code))


def test_non_retryable_status_codes_classified_non_retryable() -> None:
    for code in NON_RETRYABLE_CODES:
        assert not is_retryable(NonRetryableError("x", code=code))


def test_timeout_is_retryable() -> None:
    assert is_retryable(TimeoutError("deadline exceeded"))


def test_plain_exception_without_a_code_is_not_retryable() -> None:
    assert not is_retryable(ValueError("boom"))


@given(attempt=st.integers(min_value=0, max_value=20))
def test_backoff_within_bounds(attempt: int) -> None:
    delay = backoff_delay(attempt, base=0.5, cap=30.0)
    assert 0.0 <= delay <= 30.0


@given(attempt=st.integers(min_value=0, max_value=5))
def test_backoff_ceiling_grows_monotonically_until_capped(attempt: int) -> None:
    # The *ceiling* (not the jittered sample itself) must be monotonic —
    # sample the max achievable delay by pinning jitter to its upper bound.
    import random

    rng_state = random.getstate()
    try:
        random.seed(0)
        ceiling_now = max(backoff_delay(attempt, base=0.5, cap=30.0) for _ in range(200))
        ceiling_next = max(backoff_delay(attempt + 1, base=0.5, cap=30.0) for _ in range(200))
    finally:
        random.setstate(rng_state)
    assert ceiling_next >= ceiling_now - 1e-9


def test_backoff_respects_cap_even_at_large_attempts() -> None:
    assert backoff_delay(1000, base=0.5, cap=30.0) <= 30.0
