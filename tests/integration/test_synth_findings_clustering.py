"""Integration coverage for the phase doc's explicitly named ordering
requirement: **promotion is applied after clustering, not before.**

Two claim groups, each below `api.evidence.promotion`'s 5-support/
3-distinct-thread bar on its own, must still be promoted once genuinely
near-duplicate themes are merged into one cluster first — and, the
symmetric guard, two claim groups that combined would clear the bar must
stay unpromoted when they are not duplicates at all, so a real pain point
isn't manufactured out of two unrelated small ones.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import asyncpg
import httpx
import pytest
from _db import insert_run

from api.llm.embed import EMBEDDING_DIM, build_embed_context
from api.synth.findings import build_pain_point_findings

pytestmark = pytest.mark.usefixtures("skip_without_postgres")


async def _insert_entity(conn: asyncpg.Connection) -> int:
    key = f"category:{uuid.uuid4().hex[:12]}"
    row = await conn.fetchrow(
        "INSERT INTO entities (entity_key, display_name) VALUES ($1, $2) RETURNING id",
        key,
        "Community signal",
    )
    assert row is not None
    return int(row["id"])


async def _insert_source(conn: asyncpg.Connection, text: str) -> int:
    url = f"https://{uuid.uuid4().hex[:12]}.example.com/thread"
    row = await conn.fetchrow(
        "INSERT INTO sources (canonical_url, fetched_at, http_status, extracted_text, "
        "content_hash) VALUES ($1, now(), 200, $2, $3) RETURNING id",
        url,
        text,
        hashlib.sha256(text.encode()).hexdigest(),
    )
    assert row is not None
    return int(row["id"])


async def _insert_complaint(
    conn: asyncpg.Connection, run_id: str, entity_id: int, *, slug: str, tag: str
) -> None:
    quote = f"{tag} mention of {slug}"
    text = f"Thread {tag}: {quote} as a real problem."
    source_id = await _insert_source(conn, text)
    char_start = text.find(quote)
    assert char_start != -1
    await conn.execute(
        """
        INSERT INTO claims (run_id, entity_id, attribute, value_text, source_id, quote,
                             char_start, char_end, quote_context, context_offset, grade,
                             extractor_version, confidence)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 0, 'D', 'test@1-test', 0.4)
        """,
        run_id,
        entity_id,
        f"complaint.{slug}",
        slug,
        source_id,
        quote,
        char_start,
        char_start + len(quote),
        text,
    )


def _one_hot(index: int) -> list[float]:
    vec = [0.0] * EMBEDDING_DIM
    vec[index] = 1.0
    return vec


def _near(index: int) -> list[float]:
    """A vector close to, but not identical to, `_one_hot(index)` — cosine
    similarity comfortably above the 0.86 default threshold."""
    vec = _one_hot(index)
    vec[index] = 0.98
    vec[(index + 1) % EMBEDDING_DIM] = 0.05
    return vec


def _make_handler(vector_by_slug: dict[str, list[float]]):
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        vectors = []
        for text in body["input"]:
            slug = text.split(":", 1)[0]
            vectors.append(vector_by_slug[slug])
        return httpx.Response(
            200, json={"data": [{"embedding": v} for v in vectors], "usage": {"prompt_tokens": 5}}
        )

    return handler


async def test_two_small_near_duplicate_themes_are_promoted_once_merged(
    pg_pool: asyncpg.Pool,
) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        # 2 claims for "slow-onboarding", 3 for "confusing-onboarding-flow" —
        # 2 and 3 support, each *individually* below the 5-support bar.
        for i in range(2):
            await _insert_complaint(conn, run_id, entity_id, slug="slow-onboarding", tag=f"a{i}")
        for i in range(3):
            await _insert_complaint(
                conn, run_id, entity_id, slug="confusing-onboarding-flow", tag=f"b{i}"
            )

    vectors = {
        "slow-onboarding": _one_hot(0),
        "confusing-onboarding-flow": _near(0),
    }
    embed_ctx = build_embed_context(
        pool=pg_pool,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_make_handler(vectors))),
        api_key="test-key",
        run_id=run_id,
    )

    drafts = await build_pain_point_findings(pg_pool, run_id, embed_ctx=embed_ctx)

    assert len(drafts) == 1
    assert drafts[0].support_count == 5
    assert drafts[0].distinct_threads == 5  # 5 distinct synthetic sources, one per claim


async def test_two_small_distinct_themes_are_not_promoted_by_being_merged(
    pg_pool: asyncpg.Pool,
) -> None:
    """The over-merge guard, proven the other direction: combined the two
    groups below would clear the support bar, but since they are genuinely
    distinct (orthogonal embeddings), they must never merge — and so neither
    is promoted on its own."""
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
        entity_id = await _insert_entity(conn)
        for i in range(2):
            await _insert_complaint(conn, run_id, entity_id, slug="slow-support", tag=f"a{i}")
        for i in range(3):
            await _insert_complaint(
                conn, run_id, entity_id, slug="confusing-billing-page", tag=f"b{i}"
            )

    vectors = {
        "slow-support": _one_hot(0),
        "confusing-billing-page": _one_hot(1),  # orthogonal — must not merge
    }
    embed_ctx = build_embed_context(
        pool=pg_pool,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_make_handler(vectors))),
        api_key="test-key",
        run_id=run_id,
    )

    drafts = await build_pain_point_findings(pg_pool, run_id, embed_ctx=embed_ctx)

    assert drafts == []
