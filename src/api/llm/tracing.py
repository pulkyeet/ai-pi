"""Langfuse tracing (masterplan §6, Phase 05). Fire-and-forget by
construction: `Tracer.record()` never raises, regardless of what the
underlying SDK does — a Langfuse outage must never fail a run (phase doc,
verbatim). That guarantee lives entirely in this module (both
implementations catch everything internally) rather than being a discipline
`api.llm.gateway` has to remember at every call site.

`NoopTracer` is the default whenever `settings.langfuse_public_key`/
`langfuse_secret_key` are unset — same "`None` means unconfigured, never a
crash" pattern as `producthunt_token`/`reddit_client_id` elsewhere in
`api.config`. Free Hobby tier is 50,000 units/month; at ~65 units/run that's
comfortably above expected volume — see docs/execution_phases/phase-05-llm-gateway.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import structlog
from langfuse import Langfuse

logger = structlog.get_logger()

_TRUNCATE_CHARS = 500


def _truncate(text: str, limit: int = _TRUNCATE_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


class Tracer(Protocol):
    def record(
        self,
        *,
        run_id: str,
        task_id: int | None,
        prompt_id: str,
        prompt_version: str,
        model: str,
        variables: Mapping[str, str],
        untrusted: Mapping[str, str] | None,
        output: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        cost_usd: float,
        latency_ms: int,
    ) -> None: ...


class NoopTracer:
    def record(
        self,
        *,
        run_id: str,
        task_id: int | None,
        prompt_id: str,
        prompt_version: str,
        model: str,
        variables: Mapping[str, str],
        untrusted: Mapping[str, str] | None,
        output: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        cost_usd: float,
        latency_ms: int,
    ) -> None:
        return None


class LangfuseTracer:
    def __init__(self, *, public_key: str, secret_key: str, host: str) -> None:
        self._client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    def record(
        self,
        *,
        run_id: str,
        task_id: int | None,
        prompt_id: str,
        prompt_version: str,
        model: str,
        variables: Mapping[str, str],
        untrusted: Mapping[str, str] | None,
        output: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        cost_usd: float,
        latency_ms: int,
    ) -> None:
        try:
            trace_input = {
                "variables": dict(variables),
                "untrusted": {k: _truncate(v) for k, v in (untrusted or {}).items()},
            }
            generation = self._client.start_observation(
                name=prompt_id,
                as_type="generation",
                input=trace_input,
                output=_truncate(output),
                model=model,
                metadata={"run_id": run_id, "task_id": task_id, "prompt_version": prompt_version},
                usage_details={
                    "input": input_tokens,
                    "output": output_tokens,
                    "cache_read_input_tokens": cached_tokens,
                },
                cost_details={"total": cost_usd},
            )
            generation.end()
        except Exception:  # tracing must never fail a run — see module docstring
            logger.warning("llm.tracing_failed", prompt_id=prompt_id, run_id=run_id)


def build_tracer(*, public_key: str | None, secret_key: str | None, host: str) -> Tracer:
    if not public_key or not secret_key:
        return NoopTracer()
    try:
        return LangfuseTracer(public_key=public_key, secret_key=secret_key, host=host)
    except Exception:  # a bad Langfuse config must not block startup
        logger.warning("llm.tracing_init_failed")
        return NoopTracer()


__all__ = ["LangfuseTracer", "NoopTracer", "Tracer", "build_tracer"]
