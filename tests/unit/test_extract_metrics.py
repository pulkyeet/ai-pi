from __future__ import annotations

from api.extract.metrics import DropCounts, DropReason, ExtractionMetrics


def test_drop_counts_starts_at_zero_for_every_reason() -> None:
    counts = DropCounts()
    assert counts.total == 0
    for reason in DropReason:
        assert counts.counts[reason] == 0


def test_drop_counts_records_by_reason() -> None:
    counts = DropCounts()
    counts.record(DropReason.QUOTE_NOT_IN_SOURCE)
    counts.record(DropReason.QUOTE_NOT_IN_SOURCE)
    counts.record(DropReason.QUOTE_AMBIGUOUS)
    assert counts.counts[DropReason.QUOTE_NOT_IN_SOURCE] == 2
    assert counts.counts[DropReason.QUOTE_AMBIGUOUS] == 1
    assert counts.total == 3


def test_drop_rate_is_zero_when_nothing_returned() -> None:
    metrics = ExtractionMetrics(claims_returned_by_model=0, claims_bound=0, drops=DropCounts())
    assert metrics.drop_rate == 0.0


def test_drop_rate_is_dropped_over_returned() -> None:
    drops = DropCounts()
    drops.record(DropReason.QUOTE_NOT_IN_SOURCE)
    metrics = ExtractionMetrics(claims_returned_by_model=4, claims_bound=3, drops=drops)
    assert metrics.drop_rate == 0.25
