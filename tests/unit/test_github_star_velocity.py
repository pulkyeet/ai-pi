"""`compute_star_velocity` is a pure function extracted specifically so the
90-day-window arithmetic is unit-testable without the Starring endpoint
being reachable — which it currently isn't (see `api.sources.github`'s
module docstring and `docs/external_apis.md`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.sources.github import compute_star_velocity


def _iso(now: datetime, days_ago: int) -> str:
    return (now - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def test_all_stars_within_window() -> None:
    now = datetime(2026, 8, 6, tzinfo=UTC)
    stars = [_iso(now, d) for d in (10, 20, 30)]
    assert compute_star_velocity(stars, now=now) == pytest.approx(3 / 30)


def test_stars_outside_window_are_excluded() -> None:
    now = datetime(2026, 8, 6, tzinfo=UTC)
    stars = [_iso(now, 10), _iso(now, 200)]
    assert compute_star_velocity(stars, now=now) == pytest.approx(1 / 10)


def test_no_stars_at_all_returns_zero() -> None:
    assert compute_star_velocity([], now=datetime.now(UTC)) == 0.0


def test_no_stars_within_window_returns_zero() -> None:
    now = datetime(2026, 8, 6, tzinfo=UTC)
    assert compute_star_velocity([_iso(now, 200)], now=now) == 0.0
