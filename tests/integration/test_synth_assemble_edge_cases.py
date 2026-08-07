"""Degenerate-input branches for `api.synth.assemble`'s DB-backed helpers —
a run with no pricing claims and no dated claims at all must still produce
an honest, empty `PricingLandscape`/`Freshness`, not raise or fabricate."""

from __future__ import annotations

import asyncpg
import pytest
from _db import insert_run

from api.synth.assemble import build_freshness, build_pricing_landscape

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


async def test_pricing_landscape_with_no_pricing_claims_is_the_empty_degenerate_value(
    pg_pool: asyncpg.Pool,
) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)

    landscape = await build_pricing_landscape(pg_pool, run_id)

    assert landscape.median_entry_usd_month == 0.0
    assert landscape.spread == (0.0, 0.0)
    assert landscape.claim_ids == []


async def test_freshness_with_no_claims_is_the_empty_degenerate_value(
    pg_pool: asyncpg.Pool,
) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)

    freshness = await build_freshness(pg_pool, run_id)

    assert freshness.median_source_age_days == 0
