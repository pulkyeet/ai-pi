"""Attribute/value-type validation plus the span-binding gate (masterplan
§4.8, phase doc). Each returned claim passes four gates, in order, each with
its own drop reason:

1. **Schema** — Pydantic, at the Phase 05 gateway. `RawExtractedClaim` is
   what `structured()` validates the model's JSON against; nothing that
   fails to parse into it ever reaches this module.
2. **Vocabulary** — `attribute` is in the closed enum or matches a
   parameterised family pattern (`api.models.claims`, Phase 00).
3. **Value type** — matches `ATTRIBUTE_SPEC`.
4. **Span** — `bind_span` succeeds.

`RawExtractedClaim.attribute` is a plain `str`, not `ClaimAttribute`: an
invented attribute must reach gate 2 to be counted as `invalid_attribute`,
not fail gate 1 and burn a repair retry the model cannot satisfy (it doesn't
know the vocabulary is closed beyond the prompt's own instructions).

`ExtractedClaim` deliberately omits `run_id`, `source_id`, `entity_id`,
`grade`, `confidence`, and `superseded_by` — entity resolution (Phase 07)
and grading/confidence (Phase 08) are out of this phase's scope. It carries
`candidate_entity_hint` instead of a resolved `entity_id`, per the phase
doc's scope note.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel

from api.extract.metrics import DropReason
from api.extract.span import bind_span, quote_context_window
from api.models.claims import AttributeSpec, ValueKind, attribute_spec, validate_claim_attribute


class RawExtractedClaim(BaseModel):
    attribute: str
    value_text: str | None = None
    value_num: float | None = None
    unit: str | None = None
    as_of: date | None = None
    quote: str
    candidate_entity_hint: str | None = None


class ExtractionResponse(BaseModel):
    claims: list[RawExtractedClaim]


class ExtractedClaim(BaseModel):
    attribute: str
    value_text: str | None = None
    value_num: float | None = None
    unit: str | None = None
    as_of: date | None = None
    quote: str
    char_start: int
    char_end: int
    quote_context: str
    context_offset: int
    candidate_entity_hint: str | None = None
    extractor_version: str


@dataclass(frozen=True)
class ValidationOutcome:
    claim: ExtractedClaim | None
    drop_reason: DropReason | None


def _value_type_ok(spec: AttributeSpec, raw: RawExtractedClaim) -> bool:
    if spec.kind is ValueKind.NUMERIC:
        return raw.value_num is not None
    if spec.kind is ValueKind.BOOLEAN:
        return raw.value_text in ("true", "false")
    if spec.kind is ValueKind.ENUM:
        return raw.value_text in (spec.choices or frozenset())
    return True


def _span_drop_reason(source_text: str, quote: str) -> DropReason:
    """Only called once `bind_span` has already returned `None` — recomputes
    which of its two rejection branches fired, purely for drop-rate
    accounting. `bind_span` itself stays exactly the masterplan-spec'd
    function with no reason threaded through it (see `test_span.py`)."""
    if not quote or source_text.find(quote) == -1:
        return DropReason.QUOTE_NOT_IN_SOURCE
    return DropReason.QUOTE_AMBIGUOUS


def validate_and_bind(
    raw: RawExtractedClaim, *, source_text: str, extractor_version: str
) -> ValidationOutcome:
    try:
        attribute = validate_claim_attribute(raw.attribute)
    except ValueError:
        return ValidationOutcome(claim=None, drop_reason=DropReason.INVALID_ATTRIBUTE)

    spec = attribute_spec(attribute)
    if not _value_type_ok(spec, raw):
        return ValidationOutcome(claim=None, drop_reason=DropReason.VALUE_TYPE_MISMATCH)

    span = bind_span(source_text, raw.quote)
    if span is None:
        return ValidationOutcome(claim=None, drop_reason=_span_drop_reason(source_text, raw.quote))

    context, offset = quote_context_window(source_text, span)
    claim = ExtractedClaim(
        attribute=attribute,
        value_text=raw.value_text,
        value_num=raw.value_num,
        unit=raw.unit,
        as_of=raw.as_of,
        quote=raw.quote,
        char_start=span.start,
        char_end=span.end,
        quote_context=context,
        context_offset=offset,
        candidate_entity_hint=raw.candidate_entity_hint,
        extractor_version=extractor_version,
    )
    return ValidationOutcome(claim=claim, drop_reason=None)


__all__ = [
    "ExtractedClaim",
    "ExtractionResponse",
    "RawExtractedClaim",
    "ValidationOutcome",
    "validate_and_bind",
]
