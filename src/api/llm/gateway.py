"""`structured()` — the one interface every model call in the system must go
through (masterplan §6/§8.3, Phase 05 phase doc).

Three masterplan requirements are only enforceable at this single
chokepoint (see the phase doc's "Why a gateway rather than direct calls"):
Pydantic validation before anything touches the database, prompt-cache
prefix assembly, and injection resistance ("untrusted" is a distinct
parameter — a reviewer can grep for `untrusted=` and see every place page
content reaches a model).

Call shape, always:

    response cache hit?
      ├─ yes → return cached LLMResult (cost_usd=0), record a `cache_hit`
      │        ledger row, done — zero network calls
      └─ no  → call → parse+validate
                 ├─ ok      → cache it, record the ledger row, return
                 ├─ invalid → ONE repair retry (validation detail only,
                 │            never the raw payload, appended)
                 │   ├─ ok      → cache it, record (repaired=True), return
                 │   └─ invalid → record the ledger row (real spend
                 │                happened), raise LLMValidationError
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from api.llm import cache as response_cache
from api.llm import cost as llm_cost
from api.llm.client import LLMClient, combine_usage
from api.llm.prompts import PROMPTS_DIR, PromptRegistry, render_messages
from api.llm.tracing import Tracer, build_tracer


class LLMValidationError(Exception):
    """Raised only after the one repair retry also fails. Carries the
    validation *detail* for logs — never the raw model output, which is
    exactly the "raw JSON never crosses the module boundary" guarantee the
    phase doc requires."""

    def __init__(self, prompt_id: str, detail: str) -> None:
        super().__init__(f"{prompt_id}: {detail}")
        self.prompt_id = prompt_id
        self.detail = detail


class LLMResult[T: BaseModel](BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    value: T
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float
    latency_ms: int
    provider: str
    model: str
    prompt_version: str
    cache_hit: bool = False
    repaired: bool = False


@dataclass
class LLMContext:
    pool: asyncpg.Pool
    client: LLMClient
    registry: PromptRegistry
    tracer: Tracer
    run_id: str
    task_id: int | None = None


def build_context(
    *,
    pool: asyncpg.Pool,
    http_client: httpx.AsyncClient,
    api_key: str,
    model: str,
    run_id: str,
    task_id: int | None = None,
    prompts_dir: Path = PROMPTS_DIR,
    langfuse_public_key: str | None = None,
    langfuse_secret_key: str | None = None,
    langfuse_host: str = "https://cloud.langfuse.com",
) -> LLMContext:
    """The one place a real caller (a future Phase 10 task handler) needs to
    construct an `LLMContext` — takes plain values, not `Settings`, so this
    module stays free of a dependency on `api.config`. Building an
    `LLMContext` this way means a caller never has to import `api.llm.client`
    itself, which is what makes the `TID251` ban on that import meaningful:
    `structured()` (and this factory) are the only places in the codebase
    that need to know `LLMClient` exists at all."""
    return LLMContext(
        pool=pool,
        client=LLMClient(http_client, api_key, model),
        registry=PromptRegistry(prompts_dir),
        tracer=build_tracer(
            public_key=langfuse_public_key, secret_key=langfuse_secret_key, host=langfuse_host
        ),
        run_id=run_id,
        task_id=task_id,
    )


def _json_schema_for(schema: type[BaseModel], prompt_id: str) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": prompt_id,
            "strict": True,
            "schema": schema.model_json_schema(),
        },
    }


def _error_detail(exc: ValidationError) -> str:
    """A summary that never includes the offending input value — pydantic's
    default `str(exc)` embeds a snippet of the payload that failed
    validation, which would smuggle raw model output past the module
    boundary through the exception message."""
    parts = [
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
        for e in exc.errors(include_url=False, include_context=False, include_input=False)
    ]
    return "; ".join(parts) if parts else f"{exc.error_count()} validation error(s)"


async def structured[T: BaseModel](
    schema: type[T],
    prompt_id: str,
    variables: Mapping[str, str],
    *,
    untrusted: Mapping[str, str] | None = None,
    ctx: LLMContext,
) -> LLMResult[T]:
    template = ctx.registry.get(prompt_id)
    messages = render_messages(template, variables, untrusted)
    response_format = _json_schema_for(schema, prompt_id)
    key = response_cache.cache_key(template.version, ctx.client.model, messages)

    cached_entry = await response_cache.get(ctx.pool, key)
    if cached_entry is not None:
        result: LLMResult[T] = LLMResult(
            value=schema.model_validate_json(cached_entry.content),
            input_tokens=cached_entry.input_tokens,
            output_tokens=cached_entry.output_tokens,
            cached_tokens=cached_entry.cached_tokens,
            cost_usd=0.0,
            latency_ms=0,
            provider=ctx.client.provider,
            model=ctx.client.model,
            prompt_version=template.version,
            cache_hit=True,
        )
        await llm_cost.record_llm_call(
            ctx.pool,
            run_id=ctx.run_id,
            task_id=ctx.task_id,
            prompt_id=prompt_id,
            prompt_version=template.version,
            model=ctx.client.model,
            provider=ctx.client.provider,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cached_tokens=result.cached_tokens,
            cost_usd=0.0,
            latency_ms=0,
            cache_hit=True,
            repaired=False,
        )
        return result

    start = time.perf_counter()
    raw = await ctx.client.complete(messages, response_format=response_format)
    repaired = False

    try:
        value = schema.model_validate_json(raw.content)
    except ValidationError as exc:
        repair_messages = [
            *messages,
            {"role": "assistant", "content": raw.content},
            {
                "role": "user",
                "content": (
                    "That response did not match the required schema: "
                    f"{_error_detail(exc)}. Return corrected JSON only."
                ),
            },
        ]
        repair_raw = await ctx.client.complete(repair_messages, response_format=response_format)
        combined = combine_usage(raw, repair_raw)
        try:
            value = schema.model_validate_json(repair_raw.content)
        except ValidationError as exc2:
            latency_ms = int((time.perf_counter() - start) * 1000)
            cost_usd = llm_cost.compute_cost_usd(
                ctx.client.model,
                input_tokens=combined.input_tokens,
                output_tokens=combined.output_tokens,
                cached_tokens=combined.cached_tokens,
            )
            await llm_cost.record_llm_call(
                ctx.pool,
                run_id=ctx.run_id,
                task_id=ctx.task_id,
                prompt_id=prompt_id,
                prompt_version=template.version,
                model=ctx.client.model,
                provider=ctx.client.provider,
                input_tokens=combined.input_tokens,
                output_tokens=combined.output_tokens,
                cached_tokens=combined.cached_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                cache_hit=False,
                repaired=True,
            )
            raise LLMValidationError(prompt_id, _error_detail(exc2)) from exc2
        repaired = True
        raw = combined

    latency_ms = int((time.perf_counter() - start) * 1000)
    cost_usd = llm_cost.compute_cost_usd(
        ctx.client.model,
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cached_tokens=raw.cached_tokens,
    )

    final: LLMResult[T] = LLMResult(
        value=value,
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cached_tokens=raw.cached_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        provider=ctx.client.provider,
        model=ctx.client.model,
        prompt_version=template.version,
        repaired=repaired,
    )

    await llm_cost.record_llm_call(
        ctx.pool,
        run_id=ctx.run_id,
        task_id=ctx.task_id,
        prompt_id=prompt_id,
        prompt_version=template.version,
        model=ctx.client.model,
        provider=ctx.client.provider,
        input_tokens=final.input_tokens,
        output_tokens=final.output_tokens,
        cached_tokens=final.cached_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        cache_hit=False,
        repaired=repaired,
    )
    await response_cache.put(
        ctx.pool,
        key,
        prompt_id=prompt_id,
        prompt_version=template.version,
        model=ctx.client.model,
        content=value.model_dump_json(),
        input_tokens=final.input_tokens,
        output_tokens=final.output_tokens,
        cached_tokens=final.cached_tokens,
    )

    ctx.tracer.record(
        run_id=ctx.run_id,
        task_id=ctx.task_id,
        prompt_id=prompt_id,
        prompt_version=template.version,
        model=ctx.client.model,
        variables=variables,
        untrusted=untrusted,
        output=raw.content,
        input_tokens=final.input_tokens,
        output_tokens=final.output_tokens,
        cached_tokens=final.cached_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )

    return final


__all__ = ["LLMContext", "LLMResult", "LLMValidationError", "build_context", "structured"]
