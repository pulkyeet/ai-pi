"""Computed confidence (Phase 08, masterplan §4.6): one case per grade at
baseline, the multi-domain multiplier's cap, the distinct-**domain**-not-
distinct-**source** inflation trap, age decay, the contradiction penalty,
the 0.97 cap, and the two required property tests (bounded, monotonic).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from api.evidence.confidence import (
    BASE,
    CONFIDENCE_CAP,
    CONTRADICTION_PENALTY,
    ConfidenceInputs,
    age_days,
    confidence,
    distinct_domain_count,
)
from api.models.claims import Grade

NOW = datetime(2026, 8, 7, tzinfo=UTC)


def _inputs(
    *, grade: Grade = Grade.A, n_domains: int = 1, age: float = 0.0, contradicted: bool = False
) -> ConfidenceInputs:
    return ConfidenceInputs(
        best_grade=grade, n_distinct_domains=n_domains, age_days=age, contradicted=contradicted
    )


# ---------------------------------------------------------------------------
# baseline per grade
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("grade", [Grade.A, Grade.B, Grade.C, Grade.D])
def test_baseline_confidence_matches_base_table(grade: Grade) -> None:
    result = confidence(_inputs(grade=grade))
    assert result == pytest.approx(BASE[grade])


# ---------------------------------------------------------------------------
# multi-domain multiplier: caps at min(n-1, 4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n_domains", "expected_multi"), [(1, 1.0), (2, 1.05), (3, 1.10), (5, 1.20)]
)
def test_multi_domain_multiplier(n_domains: int, expected_multi: float) -> None:
    # Grade C (not A): keeps base * multi well under the 0.97 cap, so this
    # test isolates the multiplier from the separate cap test below.
    result = confidence(_inputs(grade=Grade.C, n_domains=n_domains))
    assert result == pytest.approx(BASE[Grade.C] * expected_multi)


def test_multi_domain_multiplier_caps_at_four_increments() -> None:
    at_five = confidence(_inputs(grade=Grade.B, n_domains=5))
    at_ten = confidence(_inputs(grade=Grade.B, n_domains=10))
    assert at_five == pytest.approx(at_ten)


# ---------------------------------------------------------------------------
# distinct domains, not distinct sources — the inflation trap
# ---------------------------------------------------------------------------


def test_three_pages_on_one_domain_count_as_one_domain() -> None:
    urls = [
        "https://acme.com/pricing",
        "https://acme.com/plans",
        "https://www.acme.com/changelog",
    ]
    assert distinct_domain_count(urls) == 1


def test_two_different_domains_count_as_two() -> None:
    urls = ["https://acme.com/pricing", "https://widget.io/pricing"]
    assert distinct_domain_count(urls) == 2


def test_unparseable_urls_are_skipped_not_counted() -> None:
    assert distinct_domain_count(["not a url", "https://acme.com"]) == 1


def test_no_urls_defaults_to_one() -> None:
    assert distinct_domain_count([]) == 1


# ---------------------------------------------------------------------------
# age decay
# ---------------------------------------------------------------------------


def test_age_decay_ordering() -> None:
    values = [confidence(_inputs(age=d)) for d in (0, 30, 90, 365, 1000)]
    assert values == sorted(values, reverse=True)
    assert values[0] == pytest.approx(BASE[Grade.A])


# ---------------------------------------------------------------------------
# contradiction penalty
# ---------------------------------------------------------------------------


def test_contradiction_penalty_is_exactly_point_six() -> None:
    clean = confidence(_inputs(contradicted=False))
    contradicted = confidence(_inputs(contradicted=True))
    assert contradicted == pytest.approx(clean * CONTRADICTION_PENALTY)


# ---------------------------------------------------------------------------
# cap
# ---------------------------------------------------------------------------


def test_cap_never_exceeded_even_at_best_case_inputs() -> None:
    result = confidence(_inputs(grade=Grade.A, n_domains=5, age=0.0, contradicted=False))
    assert result <= CONFIDENCE_CAP


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        confidence(_inputs(n_domains=0))
    with pytest.raises(ValueError):
        confidence(_inputs(age=-1))


# ---------------------------------------------------------------------------
# property tests
# ---------------------------------------------------------------------------

_grades = st.sampled_from([Grade.A, Grade.B, Grade.C, Grade.D])
_domains = st.integers(min_value=1, max_value=50)
_ages = st.floats(min_value=0, max_value=5000, allow_nan=False, allow_infinity=False)
_bools = st.booleans()


@given(grade=_grades, n_domains=_domains, age=_ages, contradicted=_bools)
def test_confidence_is_bounded(
    grade: Grade, n_domains: int, age: float, contradicted: bool
) -> None:
    result = confidence(
        _inputs(grade=grade, n_domains=n_domains, age=age, contradicted=contradicted)
    )
    assert 0 < result <= CONFIDENCE_CAP


@given(grade=_grades, n_domains=_domains, age=_ages)
def test_confidence_monotonic_in_domain_count(grade: Grade, n_domains: int, age: float) -> None:
    lower = confidence(_inputs(grade=grade, n_domains=n_domains, age=age))
    higher = confidence(_inputs(grade=grade, n_domains=n_domains + 1, age=age))
    assert higher >= lower - 1e-9


@given(grade=_grades, n_domains=_domains, age=_ages)
def test_confidence_monotonic_decreasing_in_age(grade: Grade, n_domains: int, age: float) -> None:
    sooner = confidence(_inputs(grade=grade, n_domains=n_domains, age=age))
    later = confidence(_inputs(grade=grade, n_domains=n_domains, age=age + 1))
    assert later <= sooner + 1e-9


@given(grade=_grades, n_domains=_domains, age=_ages)
def test_confidence_monotonic_in_contradiction(grade: Grade, n_domains: int, age: float) -> None:
    clean = confidence(_inputs(grade=grade, n_domains=n_domains, age=age, contradicted=False))
    contradicted = confidence(_inputs(grade=grade, n_domains=n_domains, age=age, contradicted=True))
    assert contradicted <= clean + 1e-9


# ---------------------------------------------------------------------------
# age_days helper: as_of wins over fetched_at
# ---------------------------------------------------------------------------


def test_age_days_prefers_as_of_over_fetched_at() -> None:
    result = age_days(as_of=date(2026, 7, 8), fetched_at=NOW, now=NOW)
    assert result == pytest.approx(30.0, abs=0.5)


def test_age_days_falls_back_to_fetched_at() -> None:
    result = age_days(as_of=None, fetched_at=datetime(2026, 6, 8, tzinfo=UTC), now=NOW)
    assert result == pytest.approx(60.0, abs=0.5)


def test_age_days_naive_fetched_at_is_treated_as_utc() -> None:
    naive = datetime(2026, 8, 6)
    result = age_days(as_of=None, fetched_at=naive, now=NOW)
    assert result == pytest.approx(1.0, abs=0.1)


def test_age_days_requires_at_least_one_reference() -> None:
    with pytest.raises(ValueError):
        age_days(as_of=None, fetched_at=None, now=NOW)


def test_age_days_never_negative_for_future_dates() -> None:
    result = age_days(as_of=date(2026, 12, 25), fetched_at=None, now=NOW)
    assert result == 0.0
