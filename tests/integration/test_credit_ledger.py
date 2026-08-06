"""Credit ledger accumulation (`api.search.router.credits_spent_rolling_24h`)
— sums per-call costs correctly across differing amounts (e.g. Exa's plain
vs. `contents`-enabled search modes bill differently), and excludes spend
outside the rolling 24h window the daily/global-daily allowance gates check.

The phase doc's own testing table calls this a "Unit" test ("credit
accounting sums per-call costs correctly"), but the sum itself lives in a
Postgres aggregate, not a pure function — matching Phase 03's own precedent
of noting where a test lands at a different tier than the doc's literal
label when the real implementation makes that the more honest place for it.

Every test uses a fresh, unique provider name (see `unique_provider()`): the
ledger is a real, append-only, cumulative table with no per-test isolation
(same reason `test_search_router.py` and `test_search_cache.py` use unique
queries) — a hardcoded provider name would keep accumulating across repeated
runs of this file against the same long-lived Postgres container.
"""

from __future__ import annotations

import uuid

import pytest
from _db import insert_run

from api.search.router import credits_spent_rolling_24h

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


def unique_provider(suffix: str = "") -> str:
    return f"credit-ledger-{uuid.uuid4().hex[:12]}{suffix}"


async def _spend(
    pg_pool, run_id: str, provider: str, credits_usd: float, *, hours_ago: float = 0
) -> None:
    await pg_pool.execute(
        "INSERT INTO search_credit_usage (provider, run_id, credits_usd, spent_at) "
        "VALUES ($1, $2, $3, now() - ($4 || ' hours')::interval)",
        provider,
        run_id,
        credits_usd,
        str(hours_ago),
    )


async def test_sums_differing_costs_across_calls(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    provider = unique_provider()
    await _spend(pg_pool, run_id, provider, 0.007)  # plain search
    await _spend(pg_pool, run_id, provider, 0.017)  # search + contents

    total = await credits_spent_rolling_24h(pg_pool, provider)

    assert total == 0.024


async def test_spend_outside_the_rolling_24h_window_is_excluded(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    provider = unique_provider()
    await _spend(pg_pool, run_id, provider, 0.007, hours_ago=1)
    await _spend(pg_pool, run_id, provider, 0.5, hours_ago=25)  # outside the window

    total = await credits_spent_rolling_24h(pg_pool, provider)

    assert total == 0.007


async def test_different_providers_are_tracked_independently(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    provider_1, provider_2 = unique_provider("-1"), unique_provider("-2")
    await _spend(pg_pool, run_id, provider_1, 0.1)
    await _spend(pg_pool, run_id, provider_2, 0.2)

    assert await credits_spent_rolling_24h(pg_pool, provider_1) == 0.1
    assert await credits_spent_rolling_24h(pg_pool, provider_2) == 0.2


async def test_no_spend_at_all_returns_zero(pg_pool) -> None:
    assert await credits_spent_rolling_24h(pg_pool, unique_provider()) == 0.0
