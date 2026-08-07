"""Computed confidence (masterplan §4.6, decision log #5) — implemented
verbatim as its own arithmetic formula, never a model's number:

> `0.82` emitted by an LLM is decoration, and the first competent
> interviewer asks what it was calibrated against. A formula over grade,
> source count, age and contradiction status is deterministic, tunable,
> and defensible.

Four properties worth stating because they are what make it defensible in
a conversation (phase doc's own framing): deterministic, tunable (five
named constants below, each independently adjustable in Phase 14),
explainable (`ConfidenceInputs` is stored alongside the result), and capped
below 1.0 so the system never claims certainty.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time

from pydantic import BaseModel

from api.models.claims import Grade
from api.resolve.entity_key import derive_web_key

BASE: dict[Grade, float] = {Grade.A: 0.90, Grade.B: 0.75, Grade.C: 0.55, Grade.D: 0.35}
DOMAIN_BONUS_PER_STEP = 0.05
MAX_DOMAIN_BONUS_STEPS = 4
DECAY_PER_30_DAYS = 0.98
CONTRADICTION_PENALTY = 0.6
CONFIDENCE_CAP = 0.97


class ConfidenceInputs(BaseModel):
    """Every input the formula used for one claim, stored alongside
    `Claim.confidence` (`claims.confidence_inputs`, migration `0008`) so a
    score is auditable after the fact and recomputable if the formula is
    tuned — without re-running extraction. `contradicted` starts `False` at
    claim-construction time and is flipped by
    `api.evidence.contradictions.resolve_contradictions` if this claim
    turns out to be the surfaced winner of a real disagreement.
    """

    best_grade: Grade
    n_distinct_domains: int
    age_days: float
    contradicted: bool


def confidence(inputs: ConfidenceInputs) -> float:
    if inputs.n_distinct_domains < 1:
        raise ValueError("n_distinct_domains must be >= 1: a claim always has at least one source")
    if inputs.age_days < 0:
        raise ValueError("age_days must be >= 0")
    base = BASE[inputs.best_grade]
    multi = 1 + DOMAIN_BONUS_PER_STEP * min(inputs.n_distinct_domains - 1, MAX_DOMAIN_BONUS_STEPS)
    decay = DECAY_PER_30_DAYS ** (inputs.age_days / 30)
    penalty = CONTRADICTION_PENALTY if inputs.contradicted else 1.0
    return float(min(base * multi * decay * penalty, CONFIDENCE_CAP))


def distinct_domain_count(urls: Iterable[str]) -> int:
    """Distinct **registrable** domains among `urls`, reusing Phase 07's
    PSL-aware derivation (`docs.foo.com` and `www.foo.com` collapse to one;
    two different tenants of a shared PaaS host count as two) — corroboration
    means independent sources, and three pages on the same site are one
    domain, not three (the inflation trap the phase doc calls out by name).
    A URL Phase 07's deriver can't parse is skipped rather than raised, since
    a malformed URL contributes no corroboration either way. Always returns
    at least 1: a claim always has at least one source, even if this
    function is (mis)called with none or all-unparseable URLs.
    """
    domains: set[str] = set()
    for url in urls:
        try:
            domains.add(derive_web_key(url).value)
        except ValueError:
            continue
    return max(len(domains), 1)


def age_days(
    *, as_of: date | None, fetched_at: datetime | None, now: datetime | None = None
) -> float:
    """`as_of` (the claim's own stated date) wins when present; otherwise
    `fetched_at`. A claim from a page fetched today about pricing stated in
    2024 is old evidence, not fresh evidence — `as_of` is what captures
    that (phase doc's own framing).
    """
    now = now or datetime.now(UTC)
    if as_of is not None:
        reference = datetime.combine(as_of, time.min, tzinfo=UTC)
    elif fetched_at is not None:
        reference = fetched_at if fetched_at.tzinfo else fetched_at.replace(tzinfo=UTC)
    else:
        raise ValueError("need at least one of as_of or fetched_at to compute claim age")
    return max((now - reference).total_seconds() / 86400, 0.0)


__all__ = [
    "BASE",
    "CONFIDENCE_CAP",
    "CONTRADICTION_PENALTY",
    "DECAY_PER_30_DAYS",
    "DOMAIN_BONUS_PER_STEP",
    "MAX_DOMAIN_BONUS_STEPS",
    "ConfidenceInputs",
    "age_days",
    "confidence",
    "distinct_domain_count",
]
