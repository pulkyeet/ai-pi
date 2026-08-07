"""Nightly live checks for the LLM gateway (Phase 05 phase doc's own test
table) — real OpenRouter traffic through the real `structured()` entry
point, not raw HTTP like Phase 01's spike (`spikes/llm_openrouter.py`).
Marked `live`, excluded from the default run; requires `OPENROUTER_API_KEY`
and a reachable Postgres (`TEST_DATABASE_URL`), same convention as
`tests/live/test_pathguess_hitrate.py` — build the pool directly rather than
the `skip_without_postgres` fixture, since a live-test run is presumed to
have both available.

Two checks, both from the phase doc's "Live (nightly)" row:
  1. 20 real extraction-shaped calls → schema violation rate at or below
     Phase 01's baseline (0/50 with `require_parameters: true`,
     docs/external_apis.md).
  2. 10 identical-static-prefix calls → at least one shows `cached_tokens >
     0`, proving OpenRouter's prompt cache is still hitting at all (Phase 01
     measured only 1/10 — see docs/working_knowledge.md's Known Issues — so
     the bar here is "still landing sometimes", not "landing reliably").
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import asyncpg
import httpx
import pytest
import trafilatura
from _llm_fixtures import FIXTURE_PROMPTS_DIR, EchoResult, PlanExtraction

from api.llm.gateway import LLMValidationError, build_context, structured

pytestmark = pytest.mark.live

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ai_pi_test"
)
PAGES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "pages"
MANIFEST_PATH = PAGES_DIR / "manifest.json"

# Phase 01 baseline (docs/external_apis.md): 0/50 schema violations with
# `require_parameters: true`. A little slack (well above 0) absorbs one-off
# vendor hiccups without making this flaky; a real regression would show up
# as a meaningfully non-zero rate, not one borderline call in twenty.
MAX_VIOLATION_RATE = 0.15


async def _insert_run(pool: asyncpg.Pool) -> str:
    """`llm_calls.run_id` has a real FK to `runs(id)` — mirrors
    `tests/integration/_db.py::insert_run`, duplicated rather than imported
    since `tests/live/` isn't on the same sys.path entry as
    `tests/integration/`'s own helper modules (only `tests/` itself, via
    the root `conftest.py`, is guaranteed importable from every directory)."""
    unique = uuid.uuid4().hex
    async with pool.acquire() as conn:
        user_id = await conn.fetchval(
            "INSERT INTO auth.users (email) VALUES ($1) RETURNING id", f"{unique}@example.com"
        )
        run_id = f"live_test_{unique}"
        await conn.execute(
            "INSERT INTO runs (id, user_id, query) VALUES ($1, $2, 'live test query')",
            run_id,
            user_id,
        )
    return run_id


def _load_page_snippets(n: int) -> list[str]:
    manifest = json.loads(MANIFEST_PATH.read_text())
    snippets = []
    for entry in manifest:
        if not entry.get("fetched"):
            continue
        html_path = PAGES_DIR / f"{entry['domain'].replace('.', '_')}.html"
        if not html_path.exists():
            continue
        text = trafilatura.extract(html_path.read_text(encoding="utf-8", errors="replace")) or ""
        if text:
            snippets.append(text[:1200])
    return [snippets[i % len(snippets)] for i in range(n)]


async def test_schema_violation_rate_meets_phase01_baseline() -> None:
    pool = await asyncpg.create_pool(dsn=TEST_DATABASE_URL)
    client = httpx.AsyncClient()
    try:
        run_id = await _insert_run(pool)
        ctx = build_context(
            pool=pool,
            http_client=client,
            api_key=os.environ["OPENROUTER_API_KEY"],
            model="deepseek/deepseek-v4-flash",
            run_id=run_id,
            prompts_dir=FIXTURE_PROMPTS_DIR,
        )
        snippets = _load_page_snippets(20)
        violations = 0
        for text in snippets:
            try:
                await structured(
                    PlanExtraction,
                    "extract_plan",
                    {},
                    untrusted={"page_text": text},
                    ctx=ctx,
                )
            except LLMValidationError:
                violations += 1
        rate = violations / len(snippets)
        assert rate <= MAX_VIOLATION_RATE, (
            f"schema violation rate {rate:.0%} exceeded {MAX_VIOLATION_RATE:.0%} "
            f"({violations}/{len(snippets)})"
        )
    finally:
        await client.aclose()
        await pool.close()


async def test_prompt_cache_still_hits_at_least_once_in_ten() -> None:
    pool = await asyncpg.create_pool(dsn=TEST_DATABASE_URL)
    client = httpx.AsyncClient()
    try:
        run_id = await _insert_run(pool)
        ctx = build_context(
            pool=pool,
            http_client=client,
            api_key=os.environ["OPENROUTER_API_KEY"],
            model="deepseek/deepseek-v4-flash",
            run_id=run_id,
            prompts_dir=FIXTURE_PROMPTS_DIR,
        )
        cached_counts = []
        for i in range(10):
            # a unique call_id per call keeps our own response cache
            # (api.llm.cache, keyed on the full rendered messages) from
            # short-circuiting the network call — only the static system
            # prefix needs to repeat for OpenRouter's own cache to have a
            # chance of hitting.
            result = await structured(
                EchoResult,
                "cache_probe",
                {"call_id": f"{i}-{uuid.uuid4().hex[:8]}"},
                ctx=ctx,
            )
            cached_counts.append(result.cached_tokens)
        assert any(c > 0 for c in cached_counts), (
            f"no call among 10 identical-prefix calls showed cached_tokens > 0: {cached_counts}"
        )
    finally:
        await client.aclose()
        await pool.close()
