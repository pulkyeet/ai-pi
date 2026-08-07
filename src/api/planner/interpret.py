"""Stage 0 — Interpret (masterplan §3, phase doc "Design > Stage 0"): free
text in, a typed `ResearchBrief` out, plus the computed decision of whether
to interrupt the user with a disambiguation chip.

**`keywords` is deliberately not a field of `api.models.brief.ResearchBrief`.**
The phase doc's own design sketch puts it there, but `ResearchBrief` is
reused verbatim as `Report.brief` (`api.models.report`), and
`tests/unit/test_contracts.py::test_report_parses_masterplan_section_2_literal_unmodified`
asserts `Report.model_dump()` matches the masterplan §2 JSON literal
*exactly* — a literal that has no `keywords` key. Adding the field (even
with a default) would silently start emitting it on every report and break
that frozen regression fixture. The masterplan's own output-contract JSON
agrees with the fixture, not with the phase doc's sketch. So `keywords` is
carried as a sibling value on `InterpretResult`, in the extractor-adds-a-
field style already used for `ExtractedClaim` vs `RawExtractedClaim`
(`api.extract.validate`) — the model-facing schema (`RawBrief`) is a
superset of the stored/reported contract, and this module is what splits
them apart. It is used only to seed Stage 1's task args
(`query_variants`/`keywords`), never persisted onto the brief.

**Confidence separation (masterplan §12.5, phase doc "Design > Stage 0").**
`field_confidence` here is model-reported — a deliberate, narrow exception
to "confidence is computed, never generated." It is an internal routing
signal for the disambiguation decision below, and it must never reach
`findings.confidence` (evidence confidence, `api.evidence.confidence`,
which *is* computed). The two are kept apart by type as well as by name:
nothing in this module imports `api.evidence`, and `field_confidence`'s
values never flow anywhere near a `Claim`/finding. See
`tests/unit/test_planner_confidence_separation.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field

from api.llm.gateway import LLMContext, structured
from api.models.brief import ResearchBrief

PROMPT_ID = "interpret_brief"

MAX_QUERY_CHARS = 300

# Fields whose value changing the disambiguation answer would produce a
# different task DAG (phase doc's own table, "The disambiguation decision").
# `monetisation_guess` affects synthesis framing only, never which tasks
# run, so it is absent here by construction — a field not in this set
# cannot trigger a chip no matter how low its confidence is.
PLAN_CHANGING_FIELDS: frozenset[str] = frozenset({"category", "segment", "geography"})

# A static stand-in for "hypothetically re-plan with the alternative value
# and diff the DAG" (the phase doc's stated rationale for *why* these three
# fields are plan-changing). Actually re-running Stage 1 twice per candidate
# field, per brief, just to decide whether to ask a question would make the
# disambiguation decision non-deterministic and LLM-call-costly for what is
# supposed to be a cheap routing check — and the phase doc's own test spec
# ("Plan-changing classification per field") exercises this as a pure
# function, with no model involved. So the relative "how much does this
# field reshape the plan" magnitude implied by the phase doc's rationale
# table is captured once, here, as a constant: `category` reshapes the
# whole plan, `segment` changes venue/GitHub relevance, `geography` changes
# discovery queries and source relevance to a lesser degree.
PLAN_DELTA_MAGNITUDE: dict[str, float] = {"category": 3.0, "segment": 2.0, "geography": 1.5}

LOW_CONFIDENCE_THRESHOLD = 0.6
MAX_DISAMBIGUATION_CHIPS = 2


class RejectionReason(StrEnum):
    TOO_LONG = "too_long"
    INJECTION_ATTEMPT = "injection_attempt"
    BLOCKLISTED_CATEGORY = "blocklisted_category"
    NON_PRODUCT = "non_product"


class QueryRejectedError(Exception):
    """Raised by `validate_query` — always before any model call (masterplan
    §8.3: input validation happens before the model sees anything)."""

    def __init__(self, reason: RejectionReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


_INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (all |any )?(previous|prior|above) instructions",
        r"disregard (the |all )?(above|prior|previous)",
        r"you are now",
        r"\bact as\b",
        r"system prompt",
        r"reveal your (system )?(prompt|instructions)",
        r"new instructions\s*:",
        r"\bjailbreak\b",
        r"</?(system|assistant|user)>",
    )
]

# Illustrative, not exhaustive — masterplan §8.3 only requires *a* blocklist
# of harmful categories at this layer, not a complete harm taxonomy.
_BLOCKLISTED_CATEGORY_KEYWORDS = (
    "weapon",
    "explosive",
    "firearm",
    "bioweapon",
    "malware",
    "ransomware",
    "csam",
    "child sexual",
    "human trafficking",
    "drug trafficking",
)

_NON_PRODUCT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*what('?s| is)\s+\d+\s*[\+\-\*/]\s*\d+",
        r"write me a (poem|song|story|essay)",
        r"tell me a joke",
        r"what('?s| is) the weather",
        r"who (is|was) the president",
        r"translate (this|the following)",
        r"^\s*(hi|hello|hey)[.!]?\s*$",
    )
]


def validate_query(query: str) -> None:
    """Four rejection classes, checked in a fixed order, each independently
    testable (phase doc's own test table)."""
    if len(query) > MAX_QUERY_CHARS:
        raise QueryRejectedError(
            RejectionReason.TOO_LONG, f"query exceeds {MAX_QUERY_CHARS} characters"
        )
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(query):
            raise QueryRejectedError(
                RejectionReason.INJECTION_ATTEMPT, "query resembles a prompt-injection attempt"
            )
    lowered = query.lower()
    for keyword in _BLOCKLISTED_CATEGORY_KEYWORDS:
        if keyword in lowered:
            raise QueryRejectedError(
                RejectionReason.BLOCKLISTED_CATEGORY,
                f"query matches a blocklisted category: {keyword!r}",
            )
    stripped = query.strip()
    if len(stripped) < 3:
        raise QueryRejectedError(RejectionReason.NON_PRODUCT, "query is empty or too short")
    for pattern in _NON_PRODUCT_PATTERNS:
        if pattern.search(stripped):
            raise QueryRejectedError(
                RejectionReason.NON_PRODUCT, "query does not describe a product or company idea"
            )


class RawBrief(BaseModel):
    """The Stage 0 model-facing schema — `ResearchBrief` plus `keywords`,
    the field that must not leak into the stored/reported contract (see
    module docstring)."""

    category: str
    segment: str
    geography: str
    monetisation_guess: str
    keywords: list[str] = Field(default_factory=list)
    field_confidence: dict[str, float] = Field(default_factory=dict)


@dataclass(frozen=True)
class InterpretResult:
    brief: ResearchBrief
    keywords: list[str]
    disambiguation_fields: list[str]


def is_plan_changing(field: str) -> bool:
    return field in PLAN_CHANGING_FIELDS


def select_disambiguation_fields(brief: ResearchBrief) -> list[str]:
    """Low confidence **and** plan-changing, both required (phase doc:
    "asking a user something that changes nothing is pure friction"). Ranks
    qualifying fields by `(1 - confidence) * plan_delta_magnitude` and caps
    at `MAX_DISAMBIGUATION_CHIPS` even when more fields qualify."""
    candidates = [
        field
        for field, conf in brief.field_confidence.items()
        if conf < LOW_CONFIDENCE_THRESHOLD and is_plan_changing(field)
    ]
    ranked = sorted(
        candidates,
        key=lambda f: (1 - brief.field_confidence[f]) * PLAN_DELTA_MAGNITUDE.get(f, 0.0),
        reverse=True,
    )
    return ranked[:MAX_DISAMBIGUATION_CHIPS]


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


async def interpret(query: str, *, ctx: LLMContext) -> InterpretResult:
    validate_query(query)

    result = await structured(RawBrief, PROMPT_ID, {}, untrusted={"user_query": query}, ctx=ctx)
    raw = result.value

    brief = ResearchBrief(
        category=raw.category,
        segment=raw.segment,
        geography=raw.geography,
        monetisation_guess=raw.monetisation_guess,
        field_confidence={k: _clamp_unit(v) for k, v in raw.field_confidence.items()},
    )
    return InterpretResult(
        brief=brief,
        keywords=raw.keywords,
        disambiguation_fields=select_disambiguation_fields(brief),
    )


__all__ = [
    "LOW_CONFIDENCE_THRESHOLD",
    "MAX_DISAMBIGUATION_CHIPS",
    "MAX_QUERY_CHARS",
    "PLAN_CHANGING_FIELDS",
    "PLAN_DELTA_MAGNITUDE",
    "InterpretResult",
    "PROMPT_ID",
    "QueryRejectedError",
    "RawBrief",
    "RejectionReason",
    "interpret",
    "is_plan_changing",
    "select_disambiguation_fields",
    "validate_query",
]
