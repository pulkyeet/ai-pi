"""Per-model pricing config and cost/telemetry aggregation (masterplan §6,
Phase 05). `MODEL_RATES` is the one place a vendor reprice touches — callers
never hardcode a rate. Rates below are Phase 01's real measured OpenRouter
numbers for `deepseek/deepseek-v4-flash` (docs/external_apis.md and
docs/execution_phases/README.md's cost model table), not estimates.

`cached_tokens` (from OpenRouter's `usage.prompt_tokens_details.cached_tokens`)
is a *subset* of `input_tokens`, not additive — the fresh portion of input is
priced at the input rate, the cached portion at the cheaper cached-read
rate, matching masterplan §6's ~4x saving when the cache actually lands
(Phase 01 measured only 1/10 identical-prefix calls hitting it in practice —
see `docs/working_knowledge.md` Known Issues — which is exactly why this is
tracked per call rather than assumed).
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class ModelRate:
    input_usd_per_m: float
    output_usd_per_m: float
    cached_input_usd_per_m: float


MODEL_RATES: dict[str, ModelRate] = {
    "deepseek/deepseek-v4-flash": ModelRate(
        input_usd_per_m=0.09, output_usd_per_m=0.18, cached_input_usd_per_m=0.02
    ),
    # Phase 11: complaint/request theme embeddings (api.llm.embed), via
    # OpenRouter. Embeddings bill input tokens only — output_usd_per_m and
    # cached_input_usd_per_m are never charged (embed calls are never
    # cached-token calls) but are set equal to the input rate rather than 0
    # so a future caller that mis-passes output/cached tokens overcharges
    # rather than silently undercharges.
    "openai/text-embedding-3-small": ModelRate(
        input_usd_per_m=0.02, output_usd_per_m=0.02, cached_input_usd_per_m=0.02
    ),
}


class UnknownModelError(Exception):
    def __init__(self, model: str) -> None:
        super().__init__(f"no cost rate configured for model {model!r}")
        self.model = model


def compute_cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    rates: dict[str, ModelRate] | None = None,
) -> float:
    table = rates if rates is not None else MODEL_RATES
    try:
        rate = table[model]
    except KeyError:
        raise UnknownModelError(model) from None

    fresh_input = max(input_tokens - cached_tokens, 0)
    return (
        fresh_input * rate.input_usd_per_m
        + cached_tokens * rate.cached_input_usd_per_m
        + output_tokens * rate.output_usd_per_m
    ) / 1_000_000


async def record_llm_call(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    task_id: int | None,
    prompt_id: str,
    prompt_version: str,
    model: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    cost_usd: float,
    latency_ms: int,
    cache_hit: bool,
    repaired: bool,
) -> None:
    """Attributed per call to `run_id`/`task_id`, per the phase doc's own
    requirement — `meta.cost_usd` in the report contract has to come from
    somewhere, and per-call attribution can't be reconstructed after the
    fact. Cache hits are recorded too, at `cost_usd = 0`, so
    `cache_hit_rate()` is a real aggregate over this table rather than a
    derived guess (mirrors `api.search.router`'s credit ledger, which
    records every real spend the same way)."""
    await pool.execute(
        """
        INSERT INTO llm_calls
            (run_id, task_id, prompt_id, prompt_version, model, provider,
             input_tokens, output_tokens, cached_tokens, cost_usd, latency_ms,
             cache_hit, repaired)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        """,
        run_id,
        task_id,
        prompt_id,
        prompt_version,
        model,
        provider,
        input_tokens,
        output_tokens,
        cached_tokens,
        cost_usd,
        latency_ms,
        cache_hit,
        repaired,
    )


async def run_cost_usd(pool: asyncpg.Pool, run_id: str) -> float:
    value = await pool.fetchval(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE run_id = $1", run_id
    )
    return float(value)


async def cache_hit_rate(pool: asyncpg.Pool, run_id: str) -> float:
    row = await pool.fetchrow(
        "SELECT count(*) FILTER (WHERE cache_hit) AS hits, count(*) AS total "
        "FROM llm_calls WHERE run_id = $1",
        run_id,
    )
    total = row["total"] if row else 0
    if not total:
        return 0.0
    return float(row["hits"]) / float(total)


async def repair_rate(pool: asyncpg.Pool, run_id: str) -> float:
    """Tracked, not just a fallback — a rising repair rate is a
    prompt-quality signal surfaced in Phase 14 (phase doc's own risk
    mitigation)."""
    row = await pool.fetchrow(
        "SELECT count(*) FILTER (WHERE repaired) AS repaired, count(*) AS total "
        "FROM llm_calls WHERE run_id = $1",
        run_id,
    )
    total = row["total"] if row else 0
    if not total:
        return 0.0
    return float(row["repaired"]) / float(total)


__all__ = [
    "MODEL_RATES",
    "ModelRate",
    "UnknownModelError",
    "cache_hit_rate",
    "compute_cost_usd",
    "record_llm_call",
    "repair_rate",
    "run_cost_usd",
]
