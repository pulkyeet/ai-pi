"""Nightly live check: real path-guessing hit rate across the 40-domain
benchmark corpus, against the shipped `api.retrieval.guess_path` — the same
measurement `spikes/pathguess_hitrate.py` produced for `docs/external_apis.md`
(75%, see that doc's "Fetch & path-guessing (Phase 03)" section for the
per-domain breakdown and why it differs from Phase 01's 82% baseline).

Marked `live` and excluded from the default run. A failure here means either
a vendor's pricing page genuinely changed, or a real regression in
`guess_path`/`extract_text`/`PRICE_TOKEN_RE` — not something to silently
ignore, but not something that should block an unrelated PR either.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
import pytest

from api.retrieval.fetch import HostThrottle, build_client
from api.retrieval.pathguess import guess_path
from api.retrieval.robots import RobotsCache

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "spikes"))

pytestmark = pytest.mark.live

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ai_pi_test"
)

# Measured baseline (docs/external_apis.md, Phase 03): 75% (30/40). A little
# slack below that absorbs day-to-day vendor content churn without making
# this flaky; a real regression in the fetch/extract/guess pipeline itself
# will drop well below this.
MIN_HIT_RATE = 0.65


async def _measure() -> float:
    from pricing_corpus import CORPUS  # noqa: PLC0415 — spikes/ path added above

    pool = await asyncpg.create_pool(dsn=TEST_DATABASE_URL)
    client = build_client()
    throttle = HostThrottle()
    robots = RobotsCache(client)
    sem = asyncio.Semaphore(8)
    hits = 0

    async def one(domain: str) -> None:
        nonlocal hits
        async with sem:
            try:
                result = await guess_path(
                    pool, client, throttle, robots, domain, "pricing", retrieval_reason="live_test"
                )
            except Exception:  # noqa: BLE001 — a fetch failure is just a miss here
                return
            if result.found_path is not None:
                hits += 1

    try:
        await asyncio.gather(*(one(p.domain) for p in CORPUS))
        return hits / len(CORPUS)
    finally:
        await client.aclose()
        await pool.close()


def test_pathguess_hit_rate_meets_baseline() -> None:
    rate = asyncio.run(_measure())
    assert rate >= MIN_HIT_RATE, f"path-guess hit rate {rate:.0%} fell below {MIN_HIT_RATE:.0%}"
