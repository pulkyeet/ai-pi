"""`api.synth.assemble`'s pure functions — no Postgres, no LLM: pain-point
projection from already-built findings, positioning templating, date
fallback, and the final Rule 1 assertion (both the pass and violation
paths)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from api.models.brief import ResearchBrief
from api.models.entity import Maturity
from api.models.report import (
    MVP,
    CompetitorEntry,
    CompetitorPricing,
    Coverage,
    FeatureGap,
    Freshness,
    Meta,
    PainPoint,
    PricingLandscape,
    Report,
    Risk,
)
from api.synth.assemble import (
    UnboundReportError,
    _assert_binding,
    _effective_date,
    _positioning,
    build_pain_points,
)
from api.synth.findings import Finding, FindingKind


def _pain_point_finding(
    finding_id: int, *, theme: str, support: int, threads: int, grade: str, confidence: float
) -> Finding:
    return Finding(
        id=finding_id,
        run_id="r1",
        kind=FindingKind.PAIN_POINT,
        statement=f"{support} users across {threads} threads report {theme}",
        claim_ids=[100 + finding_id, 200 + finding_id],
        support_count=support,
        confidence=confidence,
        theme=theme,
        distinct_threads=threads,
        best_grade=grade,
    )


# ---------------------------------------------------------------------------
# build_pain_points
# ---------------------------------------------------------------------------


def test_build_pain_points_projects_real_numbers_not_invented_ones() -> None:
    findings = [
        _pain_point_finding(
            1, theme="manual-entry", support=23, threads=7, grade="D", confidence=0.61
        )
    ]

    pain_points = build_pain_points(findings)

    assert len(pain_points) == 1
    pp = pain_points[0]
    assert pp.theme == "manual-entry"
    assert pp.support_count == 23
    assert pp.distinct_threads == 7
    assert pp.grade == "D"
    assert pp.confidence == pytest.approx(0.61)
    assert pp.claim_ids == [101, 201]


def test_build_pain_points_ignores_non_pain_point_findings() -> None:
    competitor = Finding(
        id=1, run_id="r1", kind=FindingKind.COMPETITOR, statement="x", claim_ids=[1]
    )
    assert build_pain_points([competitor]) == []


def test_build_pain_points_handles_multiple_findings_independently() -> None:
    findings = [
        _pain_point_finding(1, theme="a", support=5, threads=3, grade="D", confidence=0.4),
        _pain_point_finding(2, theme="b", support=9, threads=4, grade="C", confidence=0.6),
    ]
    pain_points = build_pain_points(findings)
    assert {pp.theme for pp in pain_points} == {"a", "b"}


# ---------------------------------------------------------------------------
# _positioning
# ---------------------------------------------------------------------------


def test_positioning_includes_maturity_platforms_and_pricing_model() -> None:
    text = _positioning(
        maturity=Maturity.ESTABLISHED, platforms=["ios", "web"], pricing_model="seat"
    )
    assert text.startswith("Established competitor")
    assert "ios" in text and "web" in text
    assert "seat-based pricing" in text


def test_positioning_handles_unknown_maturity_without_fabricating_a_tier() -> None:
    text = _positioning(maturity=None, platforms=[], pricing_model="flat")
    assert text.startswith("Unclassified competitor")


def test_positioning_omits_platforms_clause_when_none_known() -> None:
    text = _positioning(maturity=Maturity.INDIE, platforms=[], pricing_model=None)
    assert "on " not in text


# ---------------------------------------------------------------------------
# _effective_date
# ---------------------------------------------------------------------------


def test_effective_date_prefers_as_of() -> None:
    result = _effective_date(date(2025, 1, 1), datetime(2026, 1, 1))
    assert result == date(2025, 1, 1)


def test_effective_date_falls_back_to_fetched_at() -> None:
    result = _effective_date(None, datetime(2026, 3, 4, 12, 0))
    assert result == date(2026, 3, 4)


def test_effective_date_falls_back_to_today_when_both_missing() -> None:
    result = _effective_date(None, None)
    assert result == datetime.now(UTC).date()


# ---------------------------------------------------------------------------
# _assert_binding — the final gate
# ---------------------------------------------------------------------------


def _minimal_report(**overrides: object) -> Report:
    base = dict(
        run_id="r1",
        query="q",
        brief=ResearchBrief(category="c", segment="s", geography="g", monetisation_guess="m"),
        competitors=[],
        pricing_landscape=PricingLandscape(
            median_entry_usd_month=0.0, spread=(0.0, 0.0), claim_ids=[]
        ),
        pain_points=[],
        feature_gaps=[],
        contradictions=[],
        mvp=MVP(statement="", addresses_finding_ids=[]),
        risks=[],
        coverage=Coverage(score=1.0, failed_branches=[]),
        freshness=Freshness(median_source_age_days=0, oldest=date(2026, 1, 1)),
        meta=Meta(cost_usd=0.0, duration_s=0.0, sources_fetched=0, cache_hit_rate=0.0),
    )
    base.update(overrides)
    return Report(**base)  # type: ignore[arg-type]


def test_assert_binding_passes_on_a_fully_bound_report() -> None:
    report = _minimal_report(
        mvp=MVP(statement="Build X.", addresses_finding_ids=[1, 2, 3]),
        risks=[Risk(statement="Risk.", addresses_finding_ids=[1])],
        feature_gaps=[FeatureGap(statement="Gap.", addresses_finding_ids=[2])],
        pain_points=[
            PainPoint(
                theme="t",
                support_count=5,
                distinct_threads=3,
                grade="D",
                confidence=0.5,
                claim_ids=[1],
            )
        ],
        competitors=[
            CompetitorEntry(
                entity_key="web:x.com",
                display_name="X",
                maturity=None,
                positioning="p",
                pricing=CompetitorPricing(model="seat", entry_usd_month=5.0, free_tier=True),
                claim_ids=[1],
            )
        ],
    )
    _assert_binding(report)  # does not raise


def test_assert_binding_rejects_mvp_prose_with_no_citations() -> None:
    report = _minimal_report(mvp=MVP(statement="Unsupported claim.", addresses_finding_ids=[]))
    with pytest.raises(UnboundReportError, match="mvp"):
        _assert_binding(report)


def test_assert_binding_rejects_risk_with_no_citations() -> None:
    report = _minimal_report(risks=[Risk(statement="x", addresses_finding_ids=[])])
    with pytest.raises(UnboundReportError, match="risk"):
        _assert_binding(report)


def test_assert_binding_rejects_feature_gap_with_no_citations() -> None:
    report = _minimal_report(feature_gaps=[FeatureGap(statement="x", addresses_finding_ids=[])])
    with pytest.raises(UnboundReportError, match="feature_gap"):
        _assert_binding(report)


def test_assert_binding_rejects_pain_point_with_no_claim_ids() -> None:
    report = _minimal_report(
        pain_points=[
            PainPoint(
                theme="t",
                support_count=1,
                distinct_threads=1,
                grade="D",
                confidence=0.1,
                claim_ids=[],
            )
        ]
    )
    with pytest.raises(UnboundReportError, match="pain_point"):
        _assert_binding(report)


def test_assert_binding_rejects_competitor_with_no_claim_ids() -> None:
    report = _minimal_report(
        competitors=[
            CompetitorEntry(
                entity_key="web:x.com",
                display_name="X",
                maturity=None,
                positioning="p",
                pricing=CompetitorPricing(model="seat", entry_usd_month=5.0, free_tier=True),
                claim_ids=[],
            )
        ]
    )
    with pytest.raises(UnboundReportError, match="competitor"):
        _assert_binding(report)


def test_assert_binding_accepts_the_empty_mvp_degenerate_case() -> None:
    """An MVP that was never safely bound is represented as an empty
    statement with no citations — not a violation, an honest omission."""
    report = _minimal_report(mvp=MVP(statement="", addresses_finding_ids=[]))
    _assert_binding(report)  # does not raise
