from __future__ import annotations

from api.extract.metrics import DropReason
from api.extract.validate import RawExtractedClaim, validate_and_bind

SOURCE = "Our Pro plan is $29 per month per seat, billed monthly. A free tier is available."


def _raw(**overrides: object) -> RawExtractedClaim:
    defaults: dict[str, object] = {
        "attribute": "pricing.entry_usd_month",
        "value_num": 29,
        "quote": "$29 per month",
    }
    defaults.update(overrides)
    return RawExtractedClaim(**defaults)  # type: ignore[arg-type]


def test_valid_claim_binds_span_and_passes() -> None:
    outcome = validate_and_bind(_raw(), source_text=SOURCE, extractor_version="v1")
    assert outcome.drop_reason is None
    assert outcome.claim is not None
    assert outcome.claim.quote == "$29 per month"
    assert SOURCE[outcome.claim.char_start : outcome.claim.char_end] == "$29 per month"


def test_invalid_attribute_dropped() -> None:
    raw = _raw(attribute="pricing.made_up", value_num=None)
    outcome = validate_and_bind(raw, source_text=SOURCE, extractor_version="v1")
    assert outcome.claim is None
    assert outcome.drop_reason is DropReason.INVALID_ATTRIBUTE


def test_numeric_attribute_missing_value_num_dropped() -> None:
    outcome = validate_and_bind(_raw(value_num=None), source_text=SOURCE, extractor_version="v1")
    assert outcome.drop_reason is DropReason.VALUE_TYPE_MISMATCH


def test_boolean_attribute_wrong_value_text_dropped() -> None:
    raw = RawExtractedClaim(
        attribute="pricing.free_tier", value_text="yes", quote="A free tier is available"
    )
    outcome = validate_and_bind(raw, source_text=SOURCE, extractor_version="v1")
    assert outcome.drop_reason is DropReason.VALUE_TYPE_MISMATCH


def test_boolean_attribute_correct_value_text_passes() -> None:
    raw = RawExtractedClaim(
        attribute="pricing.free_tier", value_text="true", quote="A free tier is available"
    )
    outcome = validate_and_bind(raw, source_text=SOURCE, extractor_version="v1")
    assert outcome.claim is not None
    assert outcome.claim.value_text == "true"


def test_enum_attribute_out_of_range_dropped() -> None:
    raw = RawExtractedClaim(
        attribute="pricing.model", value_text="subscription", quote="$29 per month"
    )
    outcome = validate_and_bind(raw, source_text=SOURCE, extractor_version="v1")
    assert outcome.drop_reason is DropReason.VALUE_TYPE_MISMATCH


def test_enum_attribute_in_range_passes() -> None:
    raw = RawExtractedClaim(attribute="pricing.model", value_text="seat", quote="$29 per month")
    outcome = validate_and_bind(raw, source_text=SOURCE, extractor_version="v1")
    assert outcome.claim is not None


def test_fabricated_quote_dropped_quote_not_in_source() -> None:
    outcome = validate_and_bind(
        _raw(quote="$99 per month"), source_text=SOURCE, extractor_version="v1"
    )
    assert outcome.drop_reason is DropReason.QUOTE_NOT_IN_SOURCE


def test_ambiguous_quote_dropped() -> None:
    raw = RawExtractedClaim(attribute="pricing.model", value_text="seat", quote="seat")
    outcome = validate_and_bind(raw, source_text="seat seat", extractor_version="v1")
    assert outcome.drop_reason is DropReason.QUOTE_AMBIGUOUS


def test_attribute_gate_fires_before_value_type_and_span_gates() -> None:
    raw = RawExtractedClaim(attribute="totally.invalid", value_text=None, quote="not present")
    outcome = validate_and_bind(raw, source_text=SOURCE, extractor_version="v1")
    assert outcome.drop_reason is DropReason.INVALID_ATTRIBUTE


def test_value_type_gate_fires_before_span_gate() -> None:
    raw = _raw(value_num=None, quote="not present anywhere at all")
    outcome = validate_and_bind(raw, source_text=SOURCE, extractor_version="v1")
    assert outcome.drop_reason is DropReason.VALUE_TYPE_MISMATCH


def test_parameterised_family_attribute_validates() -> None:
    raw = RawExtractedClaim(
        attribute="feature.sso.present", value_text="true", quote="$29 per month"
    )
    outcome = validate_and_bind(raw, source_text=SOURCE, extractor_version="v1")
    assert outcome.claim is not None
    assert outcome.claim.attribute == "feature.sso.present"


def test_malformed_parameterised_slug_dropped() -> None:
    raw = RawExtractedClaim(
        attribute="feature.SSO_Support.present", value_text="true", quote="$29 per month"
    )
    outcome = validate_and_bind(raw, source_text=SOURCE, extractor_version="v1")
    assert outcome.drop_reason is DropReason.INVALID_ATTRIBUTE


def test_extractor_version_propagates_to_claim() -> None:
    outcome = validate_and_bind(
        _raw(), source_text=SOURCE, extractor_version="extract_claims@deadbeef-model-x"
    )
    assert outcome.claim is not None
    assert outcome.claim.extractor_version == "extract_claims@deadbeef-model-x"


def test_candidate_entity_hint_propagates() -> None:
    raw = _raw(candidate_entity_hint="Acme Corp")
    outcome = validate_and_bind(raw, source_text=SOURCE, extractor_version="v1")
    assert outcome.claim is not None
    assert outcome.claim.candidate_entity_hint == "Acme Corp"


def test_quote_context_window_reconstructs_around_span() -> None:
    outcome = validate_and_bind(_raw(), source_text=SOURCE, extractor_version="v1")
    assert outcome.claim is not None
    c = outcome.claim
    assert c.quote_context[c.context_offset : c.context_offset + len(c.quote)] == c.quote
