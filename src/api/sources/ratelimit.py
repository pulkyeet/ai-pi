"""A small async token bucket, shared per service (masterplan §7, phase doc's
"Rate limiting and politeness"). In-memory, one instance per process —
mirrors `api.retrieval.fetch.HostThrottle`'s scope decision and Phase 02's
`BudgetTracker`: correct for the default single-worker deployment, promoted
to something cross-process only if a real multi-worker deployment needs it.

Each retriever module declares its own limit, sourced from Phase 01's
measured values (`docs/external_apis.md`) rather than vendor docs. Where a
vendor reports remaining quota in the response itself (GitHub headers, Stack
Exchange body), that's read as ground truth *in addition to* this bucket —
the bucket exists to avoid ever bursting past the limit in the first place,
not to replace reacting to the real counter.
"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate_per_s: float, *, capacity: float | None = None) -> None:
        if rate_per_s <= 0:
            raise ValueError("rate_per_s must be positive")
        self._rate = rate_per_s
        self._capacity = capacity if capacity is not None else max(1.0, rate_per_s)
        self._tokens = self._capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self._rate
                await asyncio.sleep(wait)


__all__ = ["TokenBucket"]
