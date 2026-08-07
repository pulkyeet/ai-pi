"""The response cache: transport-level, keyed on
`hash(prompt_version + model + rendered_messages)` (masterplan §6, Phase 05
phase doc). Two purposes: (1) deterministic, zero-cost CI/benchmark replay
once a corpus has been run once; (2) a debugging dev loop doesn't re-spend
on repeated identical calls.

Distinct from Phase 06's future extraction cache (`content_hash +
extractor_version`, a *domain*-level cache one layer up — "the same page
extracted the same way", not "this exact rendered prompt"). Both exist; they
are not interchangeable (phase doc, verbatim).

**Response-cache commit policy** (phase doc's open decision #2), resolved
here: cached rows live in Postgres, mirroring `api.search.cache` — not
committed to the repo as fixture files. Committing raw LLM responses would
duplicate a replay mechanism this codebase already has at the HTTP layer:
Phase 01/04's committed VCR cassettes (`tests/fixtures/cassettes/`,
including `llm_openrouter.yaml`, recorded against this exact model) already
make OpenRouter traffic replayable with zero network and zero variance for
CI. A second, overlapping cache of parsed JSON would need its own
freshness/commit story for no real benefit — this table's job is saving
*real spend* on repeated identical calls within and across runs, not
standing in for the HTTP-level fixture corpus.

Every entry is permanent (no TTL, no expiry column): a cache hit is a
deterministic function of its key at temperature 0, so a row is never stale
— only unreachable once the key itself changes.
"""

from __future__ import annotations

import hashlib
import json

import asyncpg
from pydantic import BaseModel


class CachedResponse(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int


def cache_key(prompt_version: str, model: str, messages: list[dict[str, str]]) -> str:
    normalized = json.dumps(
        {"prompt_version": prompt_version, "model": model, "messages": messages},
        sort_keys=True,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def get(pool: asyncpg.Pool, key: str) -> CachedResponse | None:
    row = await pool.fetchrow(
        "SELECT content, input_tokens, output_tokens, cached_tokens "
        "FROM llm_response_cache WHERE cache_key = $1",
        key,
    )
    if row is None:
        return None
    return CachedResponse(
        content=row["content"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cached_tokens=row["cached_tokens"],
    )


async def put(
    pool: asyncpg.Pool,
    key: str,
    *,
    prompt_id: str,
    prompt_version: str,
    model: str,
    content: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
) -> None:
    await pool.execute(
        """
        INSERT INTO llm_response_cache
            (cache_key, prompt_id, prompt_version, model, content,
             input_tokens, output_tokens, cached_tokens, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
        ON CONFLICT (cache_key) DO NOTHING
        """,
        key,
        prompt_id,
        prompt_version,
        model,
        content,
        input_tokens,
        output_tokens,
        cached_tokens,
    )


__all__ = ["CachedResponse", "cache_key", "get", "put"]
