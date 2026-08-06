"""Retry classification and backoff policy (masterplan §4.2).

Three attempts, full-jitter exponential backoff, restricted to 429/5xx/timeout.
Full jitter (not equal jitter): with several workers hitting the same
rate-limited vendor, correlated retries are the failure mode being avoided.
"""

from __future__ import annotations

import random

MAX_ATTEMPTS = 3
DEFAULT_BASE_S = 0.5
DEFAULT_CAP_S = 30.0

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class RetryableError(Exception):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class NonRetryableError(Exception):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, NonRetryableError):
        return False
    if isinstance(exc, RetryableError):
        return True
    if isinstance(exc, TimeoutError):
        return True
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code in RETRYABLE_STATUS_CODES


def backoff_delay(
    attempt: int, *, base: float = DEFAULT_BASE_S, cap: float = DEFAULT_CAP_S
) -> float:
    """Full-jitter backoff for a 0-indexed attempt number."""
    ceiling = min(cap, base * (2**attempt))
    return random.uniform(0, ceiling)
