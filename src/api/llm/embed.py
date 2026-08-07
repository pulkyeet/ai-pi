"""OpenRouter embeddings gateway (Phase 11) — the one place `src/api/`
turns text into a vector, mirroring `api.llm.gateway.structured()`'s own
shape: a thin, cached, cost-tracked wrapper around `api.llm.client.LLMClient`
so a caller never has to touch the client directly.

**Why this exists.** Masterplan §11: "pgvector only for complaint near
duplicate detection." Complaint/request theme slugs arrive from extraction
with open slugs (`receipt-ocr-accuracy` vs `ocr-misreads-receipts` — the
masterplan's own example), so `api.synth.cluster` needs real semantic
similarity, not string matching, to merge them before promotion. No phase
before this one added an embedding provider; the model chosen —
`openai/text-embedding-3-small`, via OpenRouter's `/embeddings` endpoint —
reuses the exact vendor and credential every chat call already uses (no new
vendor line item), at $0.02/M tokens (a run's handful of short theme slugs
and quotes cost a small fraction of a cent). See `docs/tracker.md`'s Phase
11 entry for the fuller reasoning and the alternatives considered (local
n-gram hashing: weaker on synonym pairs with no shared substrings; local
sentence-transformers: a genuinely new, heavy ML dependency for one step).

Cached permanently, content-hash + model keyed — mirrors
`api.extract.cache`'s "same input, same output, forever" reasoning at
temperature-equivalent determinism (an embedding call has no temperature
knob to begin with; the vendor's own output for a fixed model and input is
effectively stable). Cost is recorded through the existing `llm_calls`
ledger (`api.llm.cost`), not a new table — `prompt_id="embed_theme"` labels
these rows distinctly, and `output_tokens`/`cached_tokens` are always 0
(embeddings bill input tokens only), so `meta.cost_usd` picks up embedding
spend for free via the same `SUM(cost_usd) WHERE run_id = $1` query
`api.cli` already runs for LLM cost.

Vectors are stored and returned as plain `list[float]` — no `numpy`/`pgvector`
Python package dependency. The `embedding_cache.embedding` column is a real
pgvector `vector(EMBEDDING_DIM)` (migration `0009`), written and read as a
Postgres vector-literal string (`'[0.1,0.2,...]'::vector`), parsed back by
hand on read. `api.synth.cluster`'s actual similarity computation happens in
plain Python over these lists, not via a pgvector `<=>` SQL query — see that
module's docstring for why (mirrors `api.evidence.contradictions`'s own
"per-attribute comparison isn't expressible in one SQL clause, so it's
Python after one fetch" precedent).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import asyncpg
import httpx

from api.llm import cost as llm_cost
from api.llm.client import LLMClient

EMBEDDING_MODEL = "openai/text-embedding-3-small"
EMBEDDING_DIM = 1536
PROMPT_ID = "embed_theme"


@dataclass
class EmbedContext:
    pool: asyncpg.Pool
    client: LLMClient
    run_id: str
    task_id: int | None = None


def build_embed_context(
    *,
    pool: asyncpg.Pool,
    http_client: httpx.AsyncClient,
    api_key: str,
    run_id: str,
    task_id: int | None = None,
    model: str = EMBEDDING_MODEL,
) -> EmbedContext:
    return EmbedContext(
        pool=pool, client=LLMClient(http_client, api_key, model), run_id=run_id, task_id=task_id
    )


def _content_key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()


def _to_vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(x) for x in vector) + "]"


def _parse_vector_literal(raw: str) -> list[float]:
    return [float(x) for x in raw.strip("[]").split(",")]


async def _get_cached(pool: asyncpg.Pool, key: str) -> list[float] | None:
    row = await pool.fetchrow(
        "SELECT embedding::text AS embedding FROM embedding_cache WHERE cache_key = $1", key
    )
    return _parse_vector_literal(row["embedding"]) if row is not None else None


async def _put_cached(pool: asyncpg.Pool, key: str, *, model: str, vector: list[float]) -> None:
    await pool.execute(
        """
        INSERT INTO embedding_cache (cache_key, model, embedding, created_at)
        VALUES ($1, $2, $3::vector, now())
        ON CONFLICT (cache_key) DO NOTHING
        """,
        key,
        model,
        _to_vector_literal(vector),
    )


async def embed_texts(texts: list[str], *, ctx: EmbedContext) -> list[list[float]]:
    """Returns one vector per input text, in the same order — cache hits and
    fresh calls transparently interleaved. Duplicate texts in `texts` are
    embedded (and billed) once; every occurrence reads the same result."""
    if not texts:
        return []

    keys = [_content_key(ctx.client.model, t) for t in texts]
    resolved: dict[str, list[float]] = {}
    for key in dict.fromkeys(keys):  # de-duplicated, order-preserving
        hit = await _get_cached(ctx.pool, key)
        if hit is not None:
            resolved[key] = hit

    # One (key, representative text) pair per still-missing key — this is
    # what actually deduplicates before spending on the vendor call. A first
    # draft de-duplicated only the *cache lookup* above and then sent one
    # copy of `texts[i]` per *position* still missing, which re-sent (and
    # re-billed) every duplicate text in the input; caught by
    # `tests/integration/test_llm_embed.py::test_embed_texts_sends_only_unique_texts_to_the_vendor`.
    missing: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, text in zip(keys, texts, strict=True):
        if key not in resolved and key not in seen:
            seen.add(key)
            missing.append((key, text))

    if missing:
        start = time.perf_counter()
        raw = await ctx.client.embed([text for _, text in missing])
        latency_ms = int((time.perf_counter() - start) * 1000)
        cost_usd = llm_cost.compute_cost_usd(
            ctx.client.model, input_tokens=raw.input_tokens, output_tokens=0, cached_tokens=0
        )
        await llm_cost.record_llm_call(
            ctx.pool,
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            prompt_id=PROMPT_ID,
            prompt_version=ctx.client.model,
            model=ctx.client.model,
            provider=ctx.client.provider,
            input_tokens=raw.input_tokens,
            output_tokens=0,
            cached_tokens=0,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            cache_hit=False,
            repaired=False,
        )
        for (key, _text), vector in zip(missing, raw.vectors, strict=True):
            resolved[key] = vector
            await _put_cached(ctx.pool, key, model=ctx.client.model, vector=vector)

    return [resolved[k] for k in keys]


__all__ = [
    "EMBEDDING_DIM",
    "EMBEDDING_MODEL",
    "EmbedContext",
    "build_embed_context",
    "embed_texts",
]
