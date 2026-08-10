from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from api.models.brief import ResearchBrief
from api.models.claims import Claim, ClaimAttribute, Grade, validate_claim_attribute
from api.models.entity import EntityKey, EntityScheme
from api.models.events import PlanCreatedEvent, RunEvent, TaskFailedEvent
from api.models.plan import Plan, PlanNode, TaskKind
from api.models.report import Report

# ---------------------------------------------------------------------------
# Round-trip: every contract model
# ---------------------------------------------------------------------------

_SAMPLE_CLAIM_KWARGS = dict(
    run_id="r_test",
    entity_id=1,
    attribute="pricing.entry_usd_month",
    value_num=5,
    source_id=1,
    quote="the price is $5",
    char_start=10,
    char_end=26,
    quote_context="...somewhere the price is $5 per month...",
    context_offset=10,
    grade=Grade.A,
    extractor_version="abc123-deepseek-v4-flash",
    confidence=0.85,
)


def _round_trips(model: object) -> None:
    cls = type(model)
    dumped = cls.model_validate(model.model_dump())  # type: ignore[attr-defined]
    assert dumped == model


def test_claim_round_trips() -> None:
    _round_trips(Claim(**_SAMPLE_CLAIM_KWARGS))


def test_research_brief_round_trips() -> None:
    _round_trips(
        ResearchBrief(
            category="expense management",
            segment="B2B, freelancers and micro SMB",
            geography="global",
            monetisation_guess="seat based SaaS",
            field_confidence={"segment": 0.55, "geography": 0.40},
        )
    )


def test_plan_round_trips() -> None:
    plan = Plan(
        nodes=[
            PlanNode(
                id="t1",
                kind=TaskKind.DISCOVER_COMPETITORS,
                args={"query_variants": ["x"]},
                budget_weight=3,
            ),
            PlanNode(
                id="t2",
                kind=TaskKind.PROFILE_PRODUCT,
                args={"entity_key": "web:x.com"},
                budget_weight=2,
            ),
        ],
        edges=[("t1", "t2")],
        total_budget_weight=5,
    )
    _round_trips(plan)


def test_events_round_trip() -> None:
    adapter = TypeAdapter(RunEvent)
    for event in (
        PlanCreatedEvent(
            run_id="r1",
            plan=Plan(
                nodes=[
                    PlanNode(
                        id="t1", kind=TaskKind.OSS_PROFILE, args={"repo": "a/b"}, budget_weight=1
                    )
                ],
                total_budget_weight=1,
            ),
        ),
        TaskFailedEvent(run_id="r1", task_id=1, kind=TaskKind.OSS_PROFILE, error="timeout"),
    ):
        dumped = adapter.validate_python(adapter.dump_python(event))
        assert dumped == event


# ---------------------------------------------------------------------------
# ClaimAttribute: closed vocabulary + parameterised families
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("attr", list(ClaimAttribute))
def test_declared_attributes_are_valid(attr: ClaimAttribute) -> None:
    assert validate_claim_attribute(attr.value) == attr.value


@pytest.mark.parametrize(
    "attr",
    [
        "feature.receipt-ocr.present",
        "complaint.receipt-ocr-accuracy",
        "request.dark-mode",
        "request.dark-mode.reactions",
    ],
)
def test_parameterised_family_shapes_are_valid(attr: str) -> None:
    assert validate_claim_attribute(attr) == attr


@pytest.mark.parametrize(
    "attr",
    [
        "pricing.something_new",
        "feature.Receipt-OCR.present",  # uppercase
        "feature.x.present",  # slug too short
        "complaint." + "a" * 41,  # slug too long
        "complaint",  # no theme
        "request.dark_mode",  # wrong separator (underscore)
        "not.even.close",
    ],
)
def test_invented_or_malformed_attributes_are_rejected(attr: str) -> None:
    with pytest.raises(ValueError):
        validate_claim_attribute(attr)


# ---------------------------------------------------------------------------
# Claim: value-type validation against ATTRIBUTE_SPEC
# ---------------------------------------------------------------------------


def test_numeric_attribute_without_value_num_fails() -> None:
    kwargs = {**_SAMPLE_CLAIM_KWARGS, "value_num": None}
    with pytest.raises(ValidationError):
        Claim(**kwargs)


def test_boolean_attribute_with_non_boolean_text_fails() -> None:
    kwargs = {
        **_SAMPLE_CLAIM_KWARGS,
        "attribute": "pricing.free_tier",
        "value_num": None,
        "value_text": "yes",
    }
    with pytest.raises(ValidationError):
        Claim(**kwargs)


def test_boolean_attribute_with_valid_text_passes() -> None:
    kwargs = {
        **_SAMPLE_CLAIM_KWARGS,
        "attribute": "pricing.free_tier",
        "value_num": None,
        "value_text": "true",
    }
    Claim(**kwargs)


def test_enum_attribute_outside_choices_fails() -> None:
    kwargs = {
        **_SAMPLE_CLAIM_KWARGS,
        "attribute": "pricing.model",
        "value_num": None,
        "value_text": "enterprise",
    }
    with pytest.raises(ValidationError):
        Claim(**kwargs)


def test_enum_attribute_free_is_accepted() -> None:
    """Phase 14 follow-up: permanently-free products (no paid tier) must be
    expressible — 'freemium' would fabricate a paid tier above the free one."""
    kwargs = {
        **_SAMPLE_CLAIM_KWARGS,
        "attribute": "pricing.model",
        "value_num": None,
        "value_text": "free",
    }
    Claim(**kwargs)


def test_enum_attribute_freemium_still_accepted() -> None:
    kwargs = {
        **_SAMPLE_CLAIM_KWARGS,
        "attribute": "pricing.model",
        "value_num": None,
        "value_text": "freemium",
    }
    Claim(**kwargs)


def test_char_end_must_exceed_char_start() -> None:
    kwargs = {**_SAMPLE_CLAIM_KWARGS, "char_start": 10, "char_end": 5}
    with pytest.raises(ValidationError):
        Claim(**kwargs)


# ---------------------------------------------------------------------------
# EntityKey: parse/format round-trip, property test
# ---------------------------------------------------------------------------

_scheme_strategy = st.sampled_from(list(EntityScheme))
_value_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-._/"),
    min_size=1,
    max_size=40,
).filter(lambda s: s.strip() != "")


@given(scheme=_scheme_strategy, value=_value_strategy)
def test_entity_key_parse_format_round_trips(scheme: EntityScheme, value: str) -> None:
    key = EntityKey(scheme=scheme, value=value)
    assert EntityKey.parse(str(key)) == key


def test_entity_key_rejects_unknown_scheme() -> None:
    with pytest.raises(ValueError):
        EntityKey.parse("ftp:example.com")


def test_entity_key_rejects_missing_value() -> None:
    with pytest.raises(ValueError):
        EntityKey.parse("web:")


# ---------------------------------------------------------------------------
# Plan: DAG validation
# ---------------------------------------------------------------------------


def _node(node_id: str, budget_weight: int = 1) -> PlanNode:
    return PlanNode(
        id=node_id, kind=TaskKind.OSS_PROFILE, args={"repo": "a/b"}, budget_weight=budget_weight
    )


def test_plan_rejects_cycle() -> None:
    with pytest.raises(ValidationError):
        Plan(
            nodes=[_node("t1"), _node("t2")],
            edges=[("t1", "t2"), ("t2", "t1")],
            total_budget_weight=2,
        )


def test_plan_rejects_edge_to_undeclared_node() -> None:
    with pytest.raises(ValidationError):
        Plan(nodes=[_node("t1")], edges=[("t1", "ghost")], total_budget_weight=1)


def test_plan_rejects_budget_mismatch() -> None:
    with pytest.raises(ValidationError):
        Plan(nodes=[_node("t1", budget_weight=1)], total_budget_weight=99)


def test_plan_rejects_duplicate_node_ids() -> None:
    with pytest.raises(ValidationError):
        Plan(nodes=[_node("t1"), _node("t1")], total_budget_weight=2)


def test_plan_accepts_valid_dag() -> None:
    Plan(nodes=[_node("t1", 2), _node("t2", 3)], edges=[("t1", "t2")], total_budget_weight=5)


# ---------------------------------------------------------------------------
# Report: masterplan §2 output contract is a regression fixture
# ---------------------------------------------------------------------------

MASTERPLAN_SECTION_2_REPORT = {
    "run_id": "r_01J000000000000000000000",
    "query": "AI expense tracker for freelancers",
    "brief": {
        "category": "expense management",
        "segment": "B2B, freelancers and micro SMB",
        "geography": "global",
        "monetisation_guess": "seat based SaaS",
        "field_confidence": {"segment": 0.55, "geography": 0.40},
    },
    "competitors": [
        {
            "entity_key": "web:expensify.com",
            "display_name": "Expensify",
            "maturity": "established",
            "positioning": "Automated receipt scanning and reimbursement for SMBs",
            "pricing": {"model": "seat", "entry_usd_month": 5, "free_tier": True},
            "claim_ids": [1204, 1207, 1233],
        }
    ],
    "pricing_landscape": {
        "median_entry_usd_month": 12,
        "spread": [0, 49],
        "claim_ids": [1204, 1301, 1402],
    },
    "pain_points": [
        {
            "theme": "manual receipt categorisation",
            "support_count": 23,
            "distinct_threads": 7,
            "grade": "D",
            "confidence": 0.61,
            "claim_ids": [1501, 1502, 1503],
        }
    ],
    "feature_gaps": [
        {
            "statement": "No competitor offers automatic mileage tracking via GPS",
            "addresses_finding_ids": [12, 19, 27],
        }
    ],
    "contradictions": [
        {
            "entity_key": "web:expensify.com",
            "attribute": "pricing.entry_usd_month",
            "values": [
                {"v": 5, "src": 88, "grade": "A", "as_of": "2026-07-30"},
                {"v": 18, "src": 142, "grade": "C", "as_of": "2025-11-02"},
            ],
        }
    ],
    "mvp": {
        "statement": "A receipt-first expense tracker with automatic mileage capture",
        "addresses_finding_ids": [12, 19, 27],
    },
    "risks": [
        {
            "statement": "Expensify's seat pricing already undercuts a freemium entrant",
            "addresses_finding_ids": [12, 19],
        }
    ],
    "coverage": {"score": 0.82, "failed_branches": ["funding"]},
    "freshness": {"median_source_age_days": 41, "oldest": "2025-03-11"},
    "meta": {"cost_usd": 0.031, "duration_s": 128, "sources_fetched": 64, "cache_hit_rate": 0.31},
}


def test_report_parses_masterplan_section_2_literal_unmodified() -> None:
    report = Report.model_validate(MASTERPLAN_SECTION_2_REPORT)
    assert report.model_dump(mode="json") == MASTERPLAN_SECTION_2_REPORT
