"""Maturity tier decision list (Phase 07): one case per rule, boundary
values on every threshold, and the insufficient-signal honest default.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.models.entity import Maturity
from api.resolve.maturity import (
    ABANDONED_MONTHS,
    ESTABLISHED_DOMAIN_YEARS,
    ESTABLISHED_STARS,
    HOBBY_DOWNLOADS_PER_MONTH,
    HOBBY_STARS,
    MaturitySignals,
    derive_maturity,
)

NOW = datetime(2026, 8, 7, tzinfo=UTC)


def _days_ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


# ---------------------------------------------------------------------------
# abandoned
# ---------------------------------------------------------------------------


def test_abandoned_stale_commit_no_liveness() -> None:
    signals = MaturitySignals(last_commit_at=_days_ago(ABANDONED_MONTHS * 30.44 + 30))
    result = derive_maturity(signals, now=NOW)
    assert result.tier is Maturity.ABANDONED
    assert result.rule.startswith("abandoned:")


def test_not_abandoned_at_exactly_the_boundary() -> None:
    signals = MaturitySignals(last_commit_at=_days_ago(ABANDONED_MONTHS * 30.44))
    result = derive_maturity(signals, now=NOW)
    assert result.tier is not Maturity.ABANDONED


def test_not_abandoned_when_stale_but_still_starred() -> None:
    signals = MaturitySignals(last_commit_at=_days_ago(ABANDONED_MONTHS * 30.44 + 30), stars=50)
    result = derive_maturity(signals, now=NOW)
    assert result.tier is not Maturity.ABANDONED


# ---------------------------------------------------------------------------
# funded
# ---------------------------------------------------------------------------


def test_funded_by_funding_claim() -> None:
    result = derive_maturity(MaturitySignals(has_funding_claim=True), now=NOW)
    assert result.tier is Maturity.FUNDED


def test_funded_by_seed_stage() -> None:
    result = derive_maturity(MaturitySignals(company_stage="seed"), now=NOW)
    assert result.tier is Maturity.FUNDED


def test_funded_by_series_stage() -> None:
    result = derive_maturity(MaturitySignals(company_stage="series-b"), now=NOW)
    assert result.tier is Maturity.FUNDED


def test_not_funded_by_unrelated_stage() -> None:
    result = derive_maturity(MaturitySignals(company_stage="bootstrapped"), now=NOW)
    assert result.tier is not Maturity.FUNDED


# ---------------------------------------------------------------------------
# established
# ---------------------------------------------------------------------------


def test_established_old_domain_and_high_stars() -> None:
    signals = MaturitySignals(
        domain_age_days=int(ESTABLISHED_DOMAIN_YEARS * 365.25) + 100,
        stars=ESTABLISHED_STARS + 1,
    )
    result = derive_maturity(signals, now=NOW)
    assert result.tier is Maturity.ESTABLISHED


def test_not_established_at_exact_domain_age_boundary() -> None:
    signals = MaturitySignals(
        domain_age_days=int(ESTABLISHED_DOMAIN_YEARS * 365.25), stars=ESTABLISHED_STARS + 1
    )
    result = derive_maturity(signals, now=NOW)
    assert result.tier is not Maturity.ESTABLISHED


def test_not_established_at_exact_stars_boundary() -> None:
    signals = MaturitySignals(
        domain_age_days=int(ESTABLISHED_DOMAIN_YEARS * 365.25) + 400, stars=ESTABLISHED_STARS
    )
    result = derive_maturity(signals, now=NOW)
    assert result.tier is not Maturity.ESTABLISHED


def test_not_established_old_domain_but_no_scale_signal() -> None:
    signals = MaturitySignals(domain_age_days=int(ESTABLISHED_DOMAIN_YEARS * 365.25) + 400)
    result = derive_maturity(signals, now=NOW)
    assert result.tier is not Maturity.ESTABLISHED


# ---------------------------------------------------------------------------
# hobby
# ---------------------------------------------------------------------------


def test_hobby_paas_subdomain() -> None:
    result = derive_maturity(MaturitySignals(is_paas_subdomain=True), now=NOW)
    assert result.tier is Maturity.HOBBY
    assert result.rule == "hobby:paas_subdomain"


def test_hobby_low_stars_and_downloads() -> None:
    signals = MaturitySignals(
        stars=HOBBY_STARS - 1, downloads_per_month=HOBBY_DOWNLOADS_PER_MONTH - 1
    )
    result = derive_maturity(signals, now=NOW)
    assert result.tier is Maturity.HOBBY
    assert result.rule == "hobby:low_scale"


def test_not_hobby_at_exact_stars_boundary() -> None:
    signals = MaturitySignals(stars=HOBBY_STARS, downloads_per_month=0)
    result = derive_maturity(signals, now=NOW)
    assert result.tier is not Maturity.HOBBY


def test_unknown_stars_and_downloads_do_not_trigger_hobby() -> None:
    """None (unknown) must never be treated as "known low" — otherwise
    every entity with no scale data at all would be misclassified hobby
    instead of honestly flagged insufficient_signal."""
    result = derive_maturity(MaturitySignals(company_stage="unrelated"), now=NOW)
    assert result.tier is not Maturity.HOBBY


# ---------------------------------------------------------------------------
# indie / insufficient_signal
# ---------------------------------------------------------------------------


def test_insufficient_signal_when_nothing_is_known() -> None:
    result = derive_maturity(MaturitySignals(), now=NOW)
    assert result.tier is Maturity.INDIE
    assert result.insufficient_signal is True


def test_indie_default_with_some_signal_but_no_rule_match() -> None:
    signals = MaturitySignals(stars=500, downloads_per_month=5_000)
    result = derive_maturity(signals, now=NOW)
    assert result.tier is Maturity.INDIE
    assert result.insufficient_signal is False
    assert result.rule == "indie:default"


# ---------------------------------------------------------------------------
# rule ordering: abandoned/funded checked before established/hobby collapse
# a case that would otherwise also match a later rule
# ---------------------------------------------------------------------------


def test_funded_takes_priority_over_hobby_signals() -> None:
    signals = MaturitySignals(has_funding_claim=True, is_paas_subdomain=True)
    result = derive_maturity(signals, now=NOW)
    assert result.tier is Maturity.FUNDED
