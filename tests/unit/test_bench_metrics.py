"""`bench.metrics` (Phase 14): hand-worked arithmetic for every masterplan
§10 metric, over synthetic `Report`/`GroundTruth` data. No Postgres, no
pipeline — these are pure functions, exercised the same way `test_confidence.py`
exercises `api.evidence.confidence`'s formula.
"""

from __future__ import annotations

from datetime import date

import pytest
from bench.loader import GroundTruth, GroundTruthFact
from bench.metrics import (
    cache_hit_rate_summary,
    competitor_recall,
    contradiction_fired,
    cost_summary,
    coverage_summary,
    extraction_drop_breakdown,
    fact_accuracy,
    latency_summary,
    planner_fallback_rate,
    precision_proxy,
    sentence_binding_rate,
    synthesis_omitted_sections,
    synthesis_rejection_rate,
)

from api.models.brief import ResearchBrief
from api.models.report import (
    MVP,
    CompetitorEntry,
    CompetitorPricing,
    Contradiction,
    ContradictionValue,
    Coverage,
    FeatureGap,
    Freshness,
    Grade,
    Meta,
    PricingLandscape,
    Report,
    Risk,
)

_BRIEF = ResearchBrief(
    category="project management",
    segment="B2B",
    geography="global",
    monetisation_guess="seat based SaaS",
    field_confidence={},
)


def _competitor(
    domain: str, *, entry_usd_month: float = 10.0, model: str = "seat"
) -> CompetitorEntry:
    return CompetitorEntry(
        entity_key=f"web:{domain}",
        display_name=domain,
        maturity=None,
        positioning="a competitor",
        pricing=CompetitorPricing(model=model, entry_usd_month=entry_usd_month, free_tier=True),
        claim_ids=[1],
    )


def _report(
    *,
    competitors: list[CompetitorEntry] | None = None,
    mvp_statement: str = "",
    mvp_finding_ids: list[int] | None = None,
    risks: list[Risk] | None = None,
    feature_gaps: list[FeatureGap] | None = None,
    contradictions: list[Contradiction] | None = None,
    failed_branches: list[str] | None = None,
    coverage_score: float = 1.0,
) -> Report:
    return Report(
        run_id="r_test",
        query="project management tool",
        brief=_BRIEF,
        competitors=competitors or [],
        pricing_landscape=PricingLandscape(
            median_entry_usd_month=10.0, spread=(0.0, 20.0), claim_ids=[]
        ),
        pain_points=[],
        feature_gaps=feature_gaps or [],
        contradictions=contradictions or [],
        mvp=MVP(statement=mvp_statement, addresses_finding_ids=mvp_finding_ids or []),
        risks=risks or [],
        coverage=Coverage(score=coverage_score, failed_branches=failed_branches or []),
        freshness=Freshness(median_source_age_days=10, oldest=date(2026, 7, 1)),
        meta=Meta(cost_usd=0.01, duration_s=100.0, sources_fetched=5, cache_hit_rate=0.5),
    )


# ---------------------------------------------------------------------------
# recall / precision / fact accuracy
# ---------------------------------------------------------------------------


def test_competitor_recall_full_hit() -> None:
    report = _report(competitors=[_competitor("asana.com"), _competitor("trello.com")])
    gt = GroundTruth(must_include=["asana.com", "trello.com"])
    assert competitor_recall(report, gt) == 1.0


def test_competitor_recall_partial_hit() -> None:
    report = _report(competitors=[_competitor("asana.com")])
    gt = GroundTruth(must_include=["asana.com", "trello.com", "monday.com", "clickup.com"])
    assert competitor_recall(report, gt) == pytest.approx(0.25)


def test_competitor_recall_vacuous_when_must_include_empty() -> None:
    report = _report(competitors=[])
    assert competitor_recall(report, GroundTruth()) == 1.0


def test_precision_proxy_perfect_when_no_known_absent_found() -> None:
    report = _report(competitors=[_competitor("asana.com")])
    gt = GroundTruth(known_absent=["stripe.com"])
    assert precision_proxy(report, gt) == 1.0


def test_precision_proxy_penalized_when_known_absent_present() -> None:
    report = _report(competitors=[_competitor("asana.com"), _competitor("stripe.com")])
    gt = GroundTruth(known_absent=["stripe.com", "shopify.com"])
    assert precision_proxy(report, gt) == pytest.approx(0.5)


def test_precision_proxy_vacuous_when_known_absent_empty() -> None:
    assert precision_proxy(_report(), GroundTruth()) == 1.0


def test_fact_accuracy_vacuous_when_no_facts() -> None:
    assert fact_accuracy(_report(), GroundTruth()) == 1.0


def test_fact_accuracy_skips_non_matching_entities_before_finding_the_right_one() -> None:
    report = _report(
        competitors=[_competitor("trello.com"), _competitor("asana.com", entry_usd_month=10.99)]
    )
    gt = GroundTruth(
        facts=[
            GroundTruthFact(
                entity="asana.com",
                attribute="pricing.entry_usd_month",
                value=10.99,
                verified_on=date(2026, 8, 1),
            )
        ]
    )
    assert fact_accuracy(report, gt) == 1.0


def test_fact_accuracy_free_tier_boolean_match() -> None:
    report = _report(competitors=[_competitor("asana.com")])  # free_tier=True by default
    gt = GroundTruth(
        facts=[
            GroundTruthFact(
                entity="asana.com",
                attribute="pricing.free_tier",
                value=True,
                verified_on=date(2026, 8, 1),
            )
        ]
    )
    assert fact_accuracy(report, gt) == 1.0


def test_fact_accuracy_unknown_attribute_is_a_miss() -> None:
    report = _report(competitors=[_competitor("asana.com")])
    gt = GroundTruth(
        facts=[
            GroundTruthFact(
                entity="asana.com",
                attribute="product.launch_date",
                value="2020-01-01",
                verified_on=date(2026, 8, 1),
            )
        ]
    )
    assert fact_accuracy(report, gt) == 0.0


def test_fact_accuracy_numeric_within_tolerance() -> None:
    report = _report(competitors=[_competitor("asana.com", entry_usd_month=10.99)])
    gt = GroundTruth(
        facts=[
            GroundTruthFact(
                entity="asana.com",
                attribute="pricing.entry_usd_month",
                value=10.99,
                verified_on=date(2026, 8, 1),
            )
        ]
    )
    assert fact_accuracy(report, gt) == 1.0


def test_fact_accuracy_numeric_outside_tolerance_fails() -> None:
    report = _report(competitors=[_competitor("asana.com", entry_usd_month=10.99)])
    gt = GroundTruth(
        facts=[
            GroundTruthFact(
                entity="asana.com",
                attribute="pricing.entry_usd_month",
                value=25.0,
                verified_on=date(2026, 8, 1),
            )
        ]
    )
    assert fact_accuracy(report, gt) == 0.0


def test_fact_accuracy_missing_entity_counts_as_miss() -> None:
    report = _report(competitors=[])
    gt = GroundTruth(
        facts=[
            GroundTruthFact(
                entity="asana.com",
                attribute="pricing.entry_usd_month",
                value=10.99,
                verified_on=date(2026, 8, 1),
            )
        ]
    )
    assert fact_accuracy(report, gt) == 0.0


def test_fact_accuracy_enum_attribute_case_insensitive() -> None:
    report = _report(competitors=[_competitor("asana.com", model="Seat")])
    gt = GroundTruth(
        facts=[
            GroundTruthFact(
                entity="asana.com",
                attribute="pricing.model",
                value="seat",
                verified_on=date(2026, 8, 1),
            )
        ]
    )
    assert fact_accuracy(report, gt) == 1.0


def test_fact_accuracy_mixed_half_correct() -> None:
    report = _report(competitors=[_competitor("asana.com", entry_usd_month=10.99)])
    gt = GroundTruth(
        facts=[
            GroundTruthFact(
                entity="asana.com",
                attribute="pricing.entry_usd_month",
                value=10.99,
                verified_on=date(2026, 8, 1),
            ),
            GroundTruthFact(
                entity="asana.com",
                attribute="pricing.entry_usd_month",
                value=999.0,
                verified_on=date(2026, 8, 1),
            ),
        ]
    )
    assert fact_accuracy(report, gt) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# sentence binding / contradictions / synthesis rejection
# ---------------------------------------------------------------------------


def test_sentence_binding_rate_all_bound() -> None:
    report = _report(
        mvp_statement="Build an OCR importer.",
        mvp_finding_ids=[1],
        risks=[Risk(statement="Vendor lock-in.", addresses_finding_ids=[2])],
    )
    assert sentence_binding_rate(report) == 1.0


def test_sentence_binding_rate_flags_an_unbound_statement() -> None:
    # A non-empty statement with zero addresses_finding_ids should never
    # happen once `api.synth.bind` enforces its own invariant correctly —
    # this is the regression guard the phase doc calls "not a tuning
    # target," proven to actually detect the broken case.
    report = _report(mvp_statement="Some unbound claim.", mvp_finding_ids=[])
    assert sentence_binding_rate(report) == 0.0


def test_sentence_binding_rate_vacuous_with_no_statements() -> None:
    assert sentence_binding_rate(_report()) == 1.0


def test_sentence_binding_rate_checks_feature_gaps_too() -> None:
    report = _report(
        feature_gaps=[
            FeatureGap(statement="Add SSO.", addresses_finding_ids=[3]),
            FeatureGap(statement="Add bulk export.", addresses_finding_ids=[]),
        ]
    )
    assert sentence_binding_rate(report) == pytest.approx(0.5)


def test_contradiction_fired_true_when_present() -> None:
    report = _report(
        contradictions=[
            Contradiction(
                entity_key="web:asana.com",
                attribute="pricing.entry_usd_month",
                values=[
                    ContradictionValue(v=5.0, src=1, grade=Grade.A, as_of=date(2026, 7, 30)),
                    ContradictionValue(v=18.0, src=2, grade=Grade.C, as_of=date(2025, 11, 2)),
                ],
            )
        ]
    )
    assert contradiction_fired(report) is True


def test_contradiction_fired_false_when_absent() -> None:
    assert contradiction_fired(_report()) is False


def test_synthesis_omitted_sections_filters_by_suffix() -> None:
    report = _report(failed_branches=["funding", "mvp_synthesis", "risks_synthesis"])
    assert synthesis_omitted_sections(report) == ["mvp_synthesis", "risks_synthesis"]


def test_synthesis_rejection_rate_across_runs() -> None:
    r1 = _report(failed_branches=["mvp_synthesis"])
    r2 = _report(failed_branches=[])
    assert synthesis_rejection_rate([r1, r2]) == pytest.approx(1 / 6)


def test_synthesis_rejection_rate_no_runs_is_zero_not_one() -> None:
    assert synthesis_rejection_rate([]) == 0.0


# ---------------------------------------------------------------------------
# process metrics: fallback, drops, cost/latency/cache/coverage summaries
# ---------------------------------------------------------------------------


def test_planner_fallback_rate() -> None:
    assert planner_fallback_rate([True, False, False, False]) == pytest.approx(0.25)


def test_planner_fallback_rate_no_runs_is_zero() -> None:
    assert planner_fallback_rate([]) == 0.0


def test_extraction_drop_breakdown_sums_across_runs() -> None:
    total = extraction_drop_breakdown(
        [
            {"quote_not_in_source": 3, "quote_ambiguous": 1},
            {"quote_not_in_source": 2, "invalid_attribute": 4},
        ]
    )
    assert total == {"quote_not_in_source": 5, "quote_ambiguous": 1, "invalid_attribute": 4}


def test_cost_summary_mean_p50_p95() -> None:
    summary = cost_summary(
        total_usd=[0.01, 0.02, 0.03, 0.04, 0.10],
        llm_usd=[0.005, 0.01, 0.015, 0.02, 0.05],
        search_usd=[0.005, 0.01, 0.015, 0.02, 0.05],
    )
    assert summary.total.mean == pytest.approx(0.04)
    assert summary.total.p50 == pytest.approx(0.03)
    assert summary.total.p95 == pytest.approx(0.10)


def test_latency_summary() -> None:
    summary = latency_summary([100.0, 150.0, 200.0])
    assert summary.mean == pytest.approx(150.0)


def test_cache_hit_rate_summary_reports_source_only() -> None:
    summary = cache_hit_rate_summary([0.5, 1.0])
    assert summary.source_mean == pytest.approx(0.75)
    assert summary.search_mean is None
    assert summary.extraction_mean is None


def test_coverage_summary_mean_and_failed_branch_tally() -> None:
    summary = coverage_summary([1.0, 0.5], [["funding"], ["funding", "mvp_synthesis"]])
    assert summary.mean == pytest.approx(0.75)
    assert summary.failed_branch_counts == {"funding": 2, "mvp_synthesis": 1}


def test_latency_summary_no_runs_is_all_zero() -> None:
    summary = latency_summary([])
    assert (summary.mean, summary.p50, summary.p95) == (0.0, 0.0, 0.0)


def test_cache_hit_rate_summary_no_runs_is_zero() -> None:
    assert cache_hit_rate_summary([]).source_mean == 0.0


def test_coverage_summary_no_runs_is_zero() -> None:
    summary = coverage_summary([], [])
    assert summary.mean == 0.0
    assert summary.failed_branch_counts == {}
