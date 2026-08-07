"""`api.synth.findings` — targeted coverage for branches the bigger pipeline
test doesn't happen to exercise: the pricing_observation entity-count gate,
and the feature_gap "already shipped somewhere" skip."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date

import asyncpg
import httpx
import pytest
from _db import insert_run

from api.llm.embed import EMBEDDING_DIM, build_embed_context
from api.synth.findings import build_feature_gap_findings, build_pricing_observation_finding

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


async def _insert_entity(conn: asyncpg.Connection, *, key: str | None = None) -> int:
    key = key or f"web:{uuid.uuid4().hex[:12]}.example.com"
    row = await conn.fetchrow(
        "INSERT INTO entities (entity_key, display_name) VALUES ($1, $2) RETURNING id",
        key,
        "Acme",
    )
    assert row is not None
    return int(row["id"])


async def _insert_source(conn: asyncpg.Connection, text: str) -> int:
    url = f"https://{uuid.uuid4().hex[:12]}.example.com/page"
    row = await conn.fetchrow(
        "INSERT INTO sources (canonical_url, fetched_at, http_status, extracted_text, "
        "content_hash) VALUES ($1, now(), 200, $2, $3) RETURNING id",
        url,
        text,
        hashlib.sha256(text.encode()).hexdigest(),
    )
    assert row is not None
    return int(row["id"])


async def _insert_claim(
    conn: asyncpg.Connection,
    *,
    run_id: str,
    entity_id: int,
    source_text: str,
    attribute: str,
    quote: str,
    value_text: str | None = None,
    value_num: float | None = None,
) -> None:
    source_id = await _insert_source(conn, source_text)
    char_start = source_text.find(quote)
    assert char_start != -1
    await conn.execute(
        """
        INSERT INTO claims (run_id, entity_id, attribute, value_text, value_num, source_id,
                             quote, char_start, char_end, quote_context, context_offset, grade,
                             extractor_version, confidence, as_of)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,0,'A','test@1-test',0.8,$11)
        """,
        run_id,
        entity_id,
        attribute,
        value_text,
        value_num,
        source_id,
        quote,
        char_start,
        char_start + len(quote),
        source_text,
        date(2026, 1, 1),
    )


async def test_pricing_observation_is_none_with_fewer_than_two_priced_entities(
    pg_pool: asyncpg.Pool,
) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        await _insert_claim(
            conn,
            run_id=run_id,
            entity_id=entity_id,
            source_text="Pricing is $9 per month here.",
            attribute="pricing.entry_usd_month",
            quote="$9 per month",
            value_num=9,
        )

    result = await build_pricing_observation_finding(pg_pool, run_id)

    assert result is None


def _one_hot_embed_handler():  # type: ignore[no-untyped-def]
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        vec = [1.0] + [0.0] * (EMBEDDING_DIM - 1)
        vectors = [vec for _ in body["input"]]
        return httpx.Response(
            200, json={"data": [{"embedding": v} for v in vectors], "usage": {"prompt_tokens": 5}}
        )

    return handler


async def test_feature_gap_already_shipped_somewhere_is_not_a_gap(pg_pool: asyncpg.Pool) -> None:
    """A request theme that would otherwise clear the promotion bar is
    skipped, not promoted, when a competitor already ships the matching
    `feature.<slug>.present` claim — the loop must actually run and hit the
    skip, not just find nothing to cluster."""
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        category_id = await _insert_entity(conn, key=f"category:{run_id}")
        competitor_id = await _insert_entity(conn)

        await _insert_claim(
            conn,
            run_id=run_id,
            entity_id=competitor_id,
            source_text="Our product supports bulk export today.",
            attribute="feature.bulk-export.present",
            quote="supports bulk export",
            value_text="true",
        )
        for i in range(5):
            await _insert_claim(
                conn,
                run_id=run_id,
                entity_id=category_id,
                source_text=f"Thread {i}: user wants bulk-export badly here.",
                attribute="request.bulk-export",
                quote="bulk-export",
                value_text="bulk-export",
            )

    embed_ctx = build_embed_context(
        pool=pg_pool,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_one_hot_embed_handler())),
        api_key="test-key",
        run_id=run_id,
    )

    drafts = await build_feature_gap_findings(
        pg_pool, run_id, embed_ctx=embed_ctx, reviewed_competitors=1
    )

    assert drafts == []
