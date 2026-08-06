"""The source cache: hit/miss/conditional accounting, negative caching, TTL
eviction, and the drill-down-survives-eviction guarantee the 500 MB mitigation
(masterplan, Supabase deviation note) depends on.
"""

from __future__ import annotations

from datetime import timedelta

import asyncpg
import httpx
import pytest
from _db import insert_run
from _http import PLAIN_HTML, ScriptedTransport, make_client, unique_root

from api.models.source import CacheOutcome
from api.retrieval import cache as source_cache
from api.retrieval.fetch import HostThrottle, fetch_source
from api.retrieval.robots import RobotsCache

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


def _throttle() -> HostThrottle:
    return HostThrottle(concurrency=2, min_gap_s=0.01)


async def test_cache_hit_makes_zero_network_calls(pg_pool) -> None:
    root = unique_root()
    transport = ScriptedTransport({"/pricing": [httpx.Response(200, content=PLAIN_HTML)]})
    client = make_client(transport)
    robots = RobotsCache(client)
    url = f"https://{root}/pricing"

    first = await fetch_source(pg_pool, client, _throttle(), robots, url, retrieval_reason="test")
    assert first.cache_outcome == CacheOutcome.MISS
    assert transport.calls["/pricing"] == 1

    second = await fetch_source(pg_pool, client, _throttle(), robots, url, retrieval_reason="test")
    assert second.cache_outcome == CacheOutcome.HIT
    assert transport.calls["/pricing"] == 1  # no second network call
    assert second.source.content_hash == first.source.content_hash
    await client.aclose()


async def test_conditional_304_refreshes_ttl_without_reextraction(pg_pool) -> None:
    root = unique_root()
    transport = ScriptedTransport({"/pricing": [httpx.Response(200, content=PLAIN_HTML)]})
    client = make_client(transport)
    robots = RobotsCache(client)
    url = f"https://{root}/pricing"

    seeded = await fetch_source(pg_pool, client, _throttle(), robots, url, retrieval_reason="test")
    assert seeded.source.content_hash is not None

    # Force the cached row stale so the next call actually goes to the network.
    await pg_pool.execute(
        "UPDATE sources SET ttl_expires_at = now() - interval '1 day' WHERE canonical_url = $1",
        url,
    )

    transport.script["/pricing"] = [httpx.Response(304)]
    refreshed = await fetch_source(
        pg_pool, client, _throttle(), robots, url, retrieval_reason="test"
    )

    assert refreshed.cache_outcome == CacheOutcome.CONDITIONAL_304
    assert refreshed.source.content_hash == seeded.source.content_hash
    assert refreshed.source.extracted_text == seeded.source.extracted_text
    assert refreshed.source.ttl_expires_at is not None
    assert seeded.source.ttl_expires_at is not None
    assert refreshed.source.ttl_expires_at > seeded.source.ttl_expires_at

    # One network call to seed, one conditional GET to refresh — never a
    # third, and never a re-extraction (asserted above via content_hash).
    assert transport.calls["/pricing"] == 2
    await client.aclose()


async def test_negative_caching_404_is_not_refetched_within_ttl(pg_pool) -> None:
    root = unique_root()
    transport = ScriptedTransport({"/missing": [httpx.Response(404)]})
    client = make_client(transport)
    robots = RobotsCache(client)
    url = f"https://{root}/missing"

    first = await fetch_source(pg_pool, client, _throttle(), robots, url, retrieval_reason="test")
    assert first.source.http_status == 404
    assert transport.calls["/missing"] == 1

    second = await fetch_source(pg_pool, client, _throttle(), robots, url, retrieval_reason="test")
    assert second.cache_outcome == CacheOutcome.HIT
    assert transport.calls["/missing"] == 1  # still just the one request
    await client.aclose()


async def test_eviction_nulls_text_on_expired_unpinned_only(pg_pool) -> None:
    unpinned_url = f"https://{unique_root()}/pricing"
    pinned_url = f"https://{unique_root()}/pricing"

    await source_cache.upsert(
        pg_pool,
        canonical_url=unpinned_url,
        root_key="example.com",
        http_status=200,
        extracted_text="unpinned text",
        content_hash="hash1",
        etag=None,
        last_modified=None,
        retrieval_reason="test",
        ttl=timedelta(hours=-1),
        is_pinned=False,
    )
    await source_cache.upsert(
        pg_pool,
        canonical_url=pinned_url,
        root_key="example.com",
        http_status=200,
        extracted_text="pinned text",
        content_hash="hash2",
        etag=None,
        last_modified=None,
        retrieval_reason="benchmark",
        ttl=timedelta(hours=-1),
        is_pinned=True,
    )

    evicted = await source_cache.evict_expired(pg_pool)
    assert evicted >= 1

    unpinned = await source_cache.get_any(pg_pool, unpinned_url)
    pinned = await source_cache.get_any(pg_pool, pinned_url)
    assert unpinned is not None and unpinned.extracted_text is None
    assert pinned is not None and pinned.extracted_text == "pinned text"


async def test_drilldown_survives_eviction_via_quote_context(pg_pool: asyncpg.Pool) -> None:
    """The 500 MB mitigation (Supabase deviation note, Phase 00's
    `claims.quote_context`/`context_offset`): once `sources.extracted_text`
    is nulled by eviction, a claim's own denormalised context window is what
    the UI falls back to for drill-down — proven here directly against the
    schema, without depending on Phase 06's extraction pipeline."""
    url = f"https://{unique_root()}/pricing"
    source = await source_cache.upsert(
        pg_pool,
        canonical_url=url,
        root_key="example.com",
        http_status=200,
        extracted_text="Full page text... Pro plan: $29/mo per seat ...more text",
        content_hash="hash3",
        etag=None,
        last_modified=None,
        retrieval_reason="test",
        ttl=timedelta(hours=-1),
        is_pinned=False,
    )

    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    entity_id = await pg_pool.fetchval(
        "INSERT INTO entities (entity_key, display_name) VALUES ($1, $2) RETURNING id",
        f"entity:{url}",
        "Test Entity",
    )
    quote_context = "...Pro plan: $29/mo per seat..."
    claim_id = await pg_pool.fetchval(
        """
        INSERT INTO claims (run_id, entity_id, attribute, value_num, source_id, quote,
                             char_start, char_end, quote_context, context_offset, grade,
                             extractor_version, confidence)
        VALUES ($1, $2, 'pricing.entry_usd_month', 29, $3, '$29/mo per seat',
                19, 34, $4, 3, 'A', 'v1', 0.9)
        RETURNING id
        """,
        run_id,
        entity_id,
        source.id,
        quote_context,
    )
    assert claim_id is not None

    evicted = await source_cache.evict_expired(pg_pool)
    assert evicted >= 1

    row = await pg_pool.fetchrow(
        "SELECT s.extracted_text AS source_text, c.quote_context, c.context_offset "
        "FROM claims c JOIN sources s ON s.id = c.source_id WHERE c.id = $1",
        claim_id,
    )
    assert row is not None
    assert row["source_text"] is None  # eviction nulled it
    assert row["quote_context"] == quote_context  # drill-down data survived
    assert "$29/mo" in row["quote_context"]
