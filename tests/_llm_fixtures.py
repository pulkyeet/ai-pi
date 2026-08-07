"""Shared Phase 05 test fixtures: a synthetic prompt (never a real domain
prompt — see `api.llm.prompts`'s module docstring) and its target schema,
used by every unit/integration/live test that exercises `structured()` end
to end without depending on Phase 06/09/11's actual prompt content."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

FIXTURE_PROMPTS_DIR = Path(__file__).resolve().parent / "fixtures" / "prompts"


class EchoResult(BaseModel):
    message: str


class BillingPeriod(StrEnum):
    MONTH = "month"
    YEAR = "year"
    ONE_TIME = "one_time"
    UNKNOWN = "unknown"


class PlanExtraction(BaseModel):
    """Mirrors `spikes/llm_openrouter.py`'s `PLAN_SCHEMA` — used by the live
    schema-violation-rate test (`tests/live/test_llm_gateway_live.py`) so
    that test measures the same kind of call Phase 01 baselined, now
    through the real gateway."""

    plan_name: str
    price_usd: float | None
    billing_period: BillingPeriod


__all__ = ["FIXTURE_PROMPTS_DIR", "BillingPeriod", "EchoResult", "PlanExtraction"]
