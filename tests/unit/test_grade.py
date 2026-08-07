"""Evidence grading (Phase 08): one case per masterplan §4.6 source type,
including the Wayback inherit-and-cap-at-B rule, plus the own-domain
prose/structured path classifier.
"""

from __future__ import annotations

import pytest

from api.evidence.grade import SourceKind, classify_own_domain_fetch, grade_for
from api.models.claims import Grade

# ---------------------------------------------------------------------------
# grade assignment table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (SourceKind.OWN_DOMAIN_STRUCTURED, Grade.A),
        (SourceKind.STRUCTURED_API, Grade.A),
        (SourceKind.OWN_DOMAIN_PROSE, Grade.B),
        (SourceKind.LAUNCH_ANNOUNCEMENT, Grade.B),
        (SourceKind.AGGREGATOR, Grade.C),
        (SourceKind.COMMUNITY, Grade.D),
    ],
)
def test_grade_assignment_table(kind: SourceKind, expected: Grade) -> None:
    assert grade_for(kind) == expected


# ---------------------------------------------------------------------------
# Wayback: inherits, capped at B
# ---------------------------------------------------------------------------


def test_wayback_of_a_grade_source_is_capped_down_to_b() -> None:
    assert grade_for(SourceKind.WAYBACK_SNAPSHOT, wayback_of=SourceKind.OWN_DOMAIN_STRUCTURED) == (
        Grade.B
    )


def test_wayback_of_a_b_grade_source_stays_b() -> None:
    assert grade_for(SourceKind.WAYBACK_SNAPSHOT, wayback_of=SourceKind.OWN_DOMAIN_PROSE) == Grade.B


def test_wayback_of_a_worse_than_b_source_keeps_the_worse_grade() -> None:
    assert grade_for(SourceKind.WAYBACK_SNAPSHOT, wayback_of=SourceKind.AGGREGATOR) == Grade.C
    assert grade_for(SourceKind.WAYBACK_SNAPSHOT, wayback_of=SourceKind.COMMUNITY) == Grade.D


def test_wayback_without_wayback_of_raises() -> None:
    with pytest.raises(ValueError, match="wayback_of"):
        grade_for(SourceKind.WAYBACK_SNAPSHOT)


def test_wayback_of_wayback_itself_raises() -> None:
    with pytest.raises(ValueError, match="wayback_of"):
        grade_for(SourceKind.WAYBACK_SNAPSHOT, wayback_of=SourceKind.WAYBACK_SNAPSHOT)


def test_wayback_of_set_on_a_non_wayback_kind_raises() -> None:
    with pytest.raises(ValueError, match="wayback_of"):
        grade_for(SourceKind.OWN_DOMAIN_STRUCTURED, wayback_of=SourceKind.AGGREGATOR)


# ---------------------------------------------------------------------------
# own-domain path classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/blog", "/blog/2026-08-07-launch", "/changelog", "/releases", "/whats-new"]
)
def test_prose_paths_classify_as_prose(path: str) -> None:
    assert classify_own_domain_fetch(path) is SourceKind.OWN_DOMAIN_PROSE


@pytest.mark.parametrize("path", ["/", "/pricing", "/plans", "/docs", "/about"])
def test_non_prose_paths_classify_as_structured(path: str) -> None:
    assert classify_own_domain_fetch(path) is SourceKind.OWN_DOMAIN_STRUCTURED


def test_path_without_leading_slash_is_normalised() -> None:
    assert classify_own_domain_fetch("blog") is SourceKind.OWN_DOMAIN_PROSE
    assert classify_own_domain_fetch("pricing") is SourceKind.OWN_DOMAIN_STRUCTURED


def test_trailing_slash_does_not_change_classification() -> None:
    assert classify_own_domain_fetch("/blog/") is SourceKind.OWN_DOMAIN_PROSE
