"""`api.llm.cost`/`api.llm.cache` against real Postgres. Every `run_id`/
`prompt_id`/`model` here is `uuid4`-suffixed per Phase 04's own lesson:
`llm_calls`/`llm_response_cache` are shared/persistent tables with no
per-test cleanup, so a literal string collides with a row a previous
invocation of this same file left behind — see
docs/working_knowledge.md's "A test against a deliberately shared/
persistent cache or ledger table needs its own uniqueness strategy" entry.
"""

from __future__ import annotations

import uuid

import pytest
from _db import insert_run, insert_task

from api.llm.cache import cache_key, get, put
from api.llm.cost import cache_hit_rate, record_llm_call, repair_rate, run_cost_usd

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def test_record_llm_call_and_run_cost_usd(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)

    await record_llm_call(
        pg_pool,
        run_id=run_id,
        task_id=None,
        prompt_id="echo",
        prompt_version="echo@aaaa1111",
        model="deepseek/deepseek-v4-flash",
        provider="openrouter",
        input_tokens=100,
        output_tokens=20,
        cached_tokens=0,
        cost_usd=0.001,
        latency_ms=500,
        cache_hit=False,
        repaired=False,
    )
    await record_llm_call(
        pg_pool,
        run_id=run_id,
        task_id=None,
        prompt_id="echo",
        prompt_version="echo@aaaa1111",
        model="deepseek/deepseek-v4-flash",
        provider="openrouter",
        input_tokens=100,
        output_tokens=20,
        cached_tokens=0,
        cost_usd=0.002,
        latency_ms=500,
        cache_hit=False,
        repaired=False,
    )

    assert await run_cost_usd(pg_pool, run_id) == pytest.approx(0.003)


async def test_record_llm_call_attributes_to_a_task(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        task_id = await insert_task(conn, run_id, "node-1")

    await record_llm_call(
        pg_pool,
        run_id=run_id,
        task_id=task_id,
        prompt_id="echo",
        prompt_version="echo@aaaa1111",
        model="deepseek/deepseek-v4-flash",
        provider="openrouter",
        input_tokens=1,
        output_tokens=1,
        cached_tokens=0,
        cost_usd=0.0,
        latency_ms=1,
        cache_hit=False,
        repaired=False,
    )

    row = await pg_pool.fetchrow("SELECT task_id FROM llm_calls WHERE run_id = $1", run_id)
    assert row is not None
    assert row["task_id"] == task_id


async def test_cache_hit_rate_and_repair_rate(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)

    async def _record(*, cache_hit: bool, repaired: bool) -> None:
        await record_llm_call(
            pg_pool,
            run_id=run_id,
            task_id=None,
            prompt_id="echo",
            prompt_version="echo@aaaa1111",
            model="deepseek/deepseek-v4-flash",
            provider="openrouter",
            input_tokens=1,
            output_tokens=1,
            cached_tokens=0,
            cost_usd=0.0,
            latency_ms=1,
            cache_hit=cache_hit,
            repaired=repaired,
        )

    await _record(cache_hit=True, repaired=False)
    await _record(cache_hit=False, repaired=True)
    await _record(cache_hit=False, repaired=False)
    await _record(cache_hit=False, repaired=False)

    assert await cache_hit_rate(pg_pool, run_id) == pytest.approx(0.25)
    assert await repair_rate(pg_pool, run_id) == pytest.approx(0.25)


async def test_rate_helpers_return_zero_for_a_run_with_no_calls(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    assert await cache_hit_rate(pg_pool, run_id) == 0.0
    assert await repair_rate(pg_pool, run_id) == 0.0


async def test_response_cache_roundtrip(pg_pool) -> None:
    key = cache_key(
        unique("echo@v"), "deepseek/deepseek-v4-flash", [{"role": "user", "content": "hi"}]
    )

    assert await get(pg_pool, key) is None

    await put(
        pg_pool,
        key,
        prompt_id="echo",
        prompt_version="echo@aaaa1111",
        model="deepseek/deepseek-v4-flash",
        content='{"message": "hi"}',
        input_tokens=10,
        output_tokens=5,
        cached_tokens=0,
    )

    cached = await get(pg_pool, key)
    assert cached is not None
    assert cached.content == '{"message": "hi"}'
    assert cached.input_tokens == 10


async def test_response_cache_put_is_idempotent_on_conflict(pg_pool) -> None:
    key = cache_key(
        unique("echo@v"), "deepseek/deepseek-v4-flash", [{"role": "user", "content": "hi"}]
    )

    await put(
        pg_pool,
        key,
        prompt_id="echo",
        prompt_version="echo@aaaa1111",
        model="deepseek/deepseek-v4-flash",
        content='{"message": "first"}',
        input_tokens=1,
        output_tokens=1,
        cached_tokens=0,
    )
    # a second write for the same key must not raise (ON CONFLICT DO NOTHING)
    await put(
        pg_pool,
        key,
        prompt_id="echo",
        prompt_version="echo@aaaa1111",
        model="deepseek/deepseek-v4-flash",
        content='{"message": "second"}',
        input_tokens=2,
        output_tokens=2,
        cached_tokens=0,
    )

    cached = await get(pg_pool, key)
    assert cached is not None
    assert cached.content == '{"message": "first"}'  # first write wins
