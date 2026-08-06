from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from api.models.brief import ResearchBrief
from api.models.claims import Grade
from api.models.entity import Maturity


class CompetitorPricing(BaseModel):
    model: str
    entry_usd_month: float
    free_tier: bool


class CompetitorEntry(BaseModel):
    entity_key: str
    display_name: str
    maturity: Maturity
    positioning: str
    pricing: CompetitorPricing
    claim_ids: list[int]


class PricingLandscape(BaseModel):
    median_entry_usd_month: float
    spread: tuple[float, float]
    claim_ids: list[int]


class PainPoint(BaseModel):
    theme: str
    support_count: int
    distinct_threads: int
    grade: Grade
    confidence: float
    claim_ids: list[int]


class FeatureGap(BaseModel):
    statement: str
    addresses_finding_ids: list[int]


class ContradictionValue(BaseModel):
    v: float
    src: int
    grade: Grade
    as_of: date


class Contradiction(BaseModel):
    entity_key: str
    attribute: str
    values: list[ContradictionValue]


class MVP(BaseModel):
    statement: str
    addresses_finding_ids: list[int]


class Risk(BaseModel):
    statement: str
    addresses_finding_ids: list[int]


class Coverage(BaseModel):
    score: float
    failed_branches: list[str]


class Freshness(BaseModel):
    median_source_age_days: int
    oldest: date


class Meta(BaseModel):
    cost_usd: float
    duration_s: float
    sources_fetched: int
    cache_hit_rate: float


class Report(BaseModel):
    """The masterplan §2 output contract, verbatim, as nested models.

    This is the acceptance target for Phase 11. `tests/unit/test_contracts.py`
    parses a fixture derived from the masterplan §2 JSON literal as a
    regression test against contract drift.
    """

    run_id: str
    query: str
    brief: ResearchBrief
    competitors: list[CompetitorEntry]
    pricing_landscape: PricingLandscape
    pain_points: list[PainPoint]
    feature_gaps: list[FeatureGap]
    contradictions: list[Contradiction]
    mvp: MVP
    risks: list[Risk]
    coverage: Coverage
    freshness: Freshness
    meta: Meta
