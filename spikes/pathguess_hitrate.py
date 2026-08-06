"""Phase 03 measurement: real path-guessing hit rate for the 7-candidate
`PRICING_PATHS` list (docs/execution_phases/phase-03-fetch-source-cache.md)
against the same 40-domain corpus Phase 01 used for its 3-candidate, 82%
baseline (`pricing_corpus.py`). This is the number the phase doc's own exit
criterion requires be "measured and written into docs/external_apis.md" —
run for real against `api.retrieval.guess_path`, not estimated.

Run: uv run python spikes/pathguess_hitrate.py
"""

from __future__ import annotations

import asyncio
import os

import asyncpg
from _common import hr
from pricing_corpus import CORPUS

from api.retrieval.fetch import HostThrottle, build_client
from api.retrieval.pathguess import PRICING_PATHS, guess_path
from api.retrieval.robots import RobotsCache

DSN = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ai_pi")


async def main() -> None:
    pool = await asyncpg.create_pool(dsn=DSN)
    client = build_client()
    throttle = HostThrottle()
    robots = RobotsCache(client)
    sem = asyncio.Semaphore(8)

    results: list[tuple[str, str, str | None]] = []

    async def one(domain: str, known_path: str) -> None:
        async with sem:
            try:
                result = await guess_path(
                    pool,
                    client,
                    throttle,
                    robots,
                    domain,
                    "pricing",
                    retrieval_reason="phase03_pathguess_measurement",
                )
                results.append((domain, known_path, result.found_path))
            except Exception as exc:  # noqa: BLE001 — measurement script, log and move on
                results.append((domain, known_path, f"ERROR: {exc}"))

    try:
        hr(f"Path-guessing hit rate — {len(PRICING_PATHS)}-candidate PRICING_PATHS vs 40-domain corpus")
        await asyncio.gather(*(one(p.domain, p.known_path) for p in CORPUS))

        by_domain = {d: (k, f) for d, k, f in results}
        hits = 0
        for page in CORPUS:
            known, found = by_domain[page.domain]
            hit = found is not None and not str(found).startswith("ERROR")
            hits += hit
            flag = "OK  " if hit else "MISS"
            print(f"  [{flag}] {page.domain:<20} known={known:<28} guessed={found}")

        rate = hits / len(CORPUS)
        print(f"\npath-guess hit rate: {hits}/{len(CORPUS)} = {rate:.0%}")
    finally:
        await client.aclose()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
