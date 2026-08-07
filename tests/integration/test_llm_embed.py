"""`api.llm.embed` against a scripted OpenRouter transport plus real
Postgres — mirrors `test_llm_gateway.py`'s own shape one layer over.

Every test embeds a `uuid4`-suffixed text, never a literal: `embedding_cache`
is deliberately keyed without `run_id` (same "a repeated call is nearly
free" reasoning as `llm_response_cache`), so two tests reusing the same
literal text collide on the same cache row in this long-lived Postgres
container — the exact lesson every prior phase's own integration suite
already recorded (docs/working_knowledge.md).
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from _db import insert_run
from _http import ScriptedTransport, make_client

from api.llm.embed import EMBEDDING_DIM, build_embed_context, embed_texts

pytestmark = pytest.mark.usefixtures("skip_without_postgres")

EMBEDDINGS_PATH = "/api/v1/embeddings"


def unique_text() -> str:
    return f"theme-{uuid.uuid4().hex[:12]}"


def _vec(*head: float) -> list[float]:
    """A real `EMBEDDING_DIM`-length vector — the `embedding_cache.embedding`
    column is a fixed-dimension pgvector type, so a short test vector is
    rejected by Postgres, not just semantically wrong."""
    return [*head, *([0.0] * (EMBEDDING_DIM - len(head)))]


def _response(vectors: list[list[float]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": [{"embedding": v} for v in vectors], "usage": {"prompt_tokens": 12}},
    )


async def test_embed_texts_returns_vectors_in_order(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    transport = ScriptedTransport({EMBEDDINGS_PATH: [_response([_vec(0.1, 0.2), _vec(0.3, 0.4)])]})
    ctx = build_embed_context(
        pool=pg_pool, http_client=make_client(transport), api_key="test-key", run_id=run_id
    )

    result = await embed_texts([unique_text(), unique_text()], ctx=ctx)

    assert result == [_vec(0.1, 0.2), _vec(0.3, 0.4)]
    assert transport.calls[EMBEDDINGS_PATH] == 1


async def test_embed_texts_caches_repeated_text_across_calls(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    text = unique_text()
    transport = ScriptedTransport({EMBEDDINGS_PATH: [_response([_vec(0.5, 0.6)])]})
    ctx = build_embed_context(
        pool=pg_pool, http_client=make_client(transport), api_key="test-key", run_id=run_id
    )

    first = await embed_texts([text], ctx=ctx)
    second = await embed_texts([text], ctx=ctx)

    assert first == second == [_vec(0.5, 0.6)]
    assert transport.calls[EMBEDDINGS_PATH] == 1  # second call was served from cache


async def test_embed_texts_sends_only_unique_texts_to_the_vendor(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    text = unique_text()
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _response([_vec(0.1, 0.2)])

    ctx = build_embed_context(
        pool=pg_pool,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        api_key="test-key",
        run_id=run_id,
    )

    result = await embed_texts([text, text], ctx=ctx)

    assert result == [_vec(0.1, 0.2), _vec(0.1, 0.2)]
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["input"] == [text]  # deduplicated before ever reaching the vendor


async def test_embed_texts_records_cost_against_the_embedding_model(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    transport = ScriptedTransport({EMBEDDINGS_PATH: [_response([_vec(0.1, 0.2)])]})
    ctx = build_embed_context(
        pool=pg_pool, http_client=make_client(transport), api_key="test-key", run_id=run_id
    )

    await embed_texts([unique_text()], ctx=ctx)

    row = await pg_pool.fetchrow(
        "SELECT model, prompt_id, cost_usd, output_tokens, cached_tokens "
        "FROM llm_calls WHERE run_id = $1",
        run_id,
    )
    assert row is not None
    assert row["model"] == "openai/text-embedding-3-small"
    assert row["prompt_id"] == "embed_theme"
    assert float(row["cost_usd"]) > 0
    assert row["output_tokens"] == 0
    assert row["cached_tokens"] == 0


async def test_embed_texts_empty_input_makes_no_call(pg_pool) -> None:
    async with pg_pool.acquire() as conn:
        run_id = await insert_run(conn)
    transport = ScriptedTransport({})
    ctx = build_embed_context(
        pool=pg_pool, http_client=make_client(transport), api_key="test-key", run_id=run_id
    )

    assert await embed_texts([], ctx=ctx) == []
    assert transport.calls[EMBEDDINGS_PATH] == 0
