"""`TokenBucket` timing, at a scaled-down real interval rather than a fake
clock — the same style Phase 03 used for `HostThrottle`'s per-host gap test
(`tests/integration/test_fetch.py::test_per_host_politeness_serialises_with_a_minimum_gap`)."""

from __future__ import annotations

import asyncio
import time

import pytest

from api.sources.ratelimit import TokenBucket


async def test_serialises_a_burst_with_correct_spacing() -> None:
    bucket = TokenBucket(rate_per_s=10.0, capacity=1)  # one refill every 0.1s
    starts: list[float] = []

    async def one() -> None:
        await bucket.acquire()
        starts.append(time.monotonic())

    await asyncio.gather(*(one() for _ in range(4)))
    starts.sort()
    gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    assert all(gap >= 0.1 - 0.03 for gap in gaps), gaps


async def test_burst_up_to_capacity_is_immediate_then_throttles() -> None:
    bucket = TokenBucket(rate_per_s=10.0, capacity=3)
    start = time.monotonic()
    for _ in range(3):
        await bucket.acquire()
    assert time.monotonic() - start < 0.05

    await bucket.acquire()  # the 4th call must wait for a refill
    assert time.monotonic() - start >= 0.1 - 0.03


def test_rejects_non_positive_rate() -> None:
    with pytest.raises(ValueError):
        TokenBucket(0)
