"""`api.synth.generate`'s pure validation logic, plus the short-circuit
guarantee: when the finding set structurally cannot satisfy the >=3-findings
/ >=1-complaint rule, no LLM call is made at all (§4.9's guard, cheaply
enforced before spending anything)."""

from __future__ import annotations

import pytest

from api.synth.findings import Finding, FindingKind
from api.synth.generate import (
    MIN_FINDINGS_REFERENCED,
    _can_possibly_satisfy,
    _violation,
    generate_feature_gaps,
    generate_mvp,
    generate_risks,
)


def _finding(finding_id: int, kind: FindingKind) -> Finding:
    return Finding(id=finding_id, run_id="r1", kind=kind, statement="x", claim_ids=[1])


PAIN = FindingKind.PAIN_POINT
COMPETITOR = FindingKind.COMPETITOR


# ---------------------------------------------------------------------------
# _violation — the aggregate pre-filter
# ---------------------------------------------------------------------------


def test_violation_none_when_three_findings_and_one_complaint_cited() -> None:
    assert _violation([1, 2, 3], all_ids={1, 2, 3, 4}, complaint_ids={1}) is None


def test_violation_flags_unknown_finding_ids() -> None:
    reason = _violation([1, 999], all_ids={1, 2, 3}, complaint_ids={1})
    assert reason is not None
    assert "unknown" in reason


def test_violation_flags_fewer_than_minimum_findings() -> None:
    reason = _violation([1, 2], all_ids={1, 2, 3, 4}, complaint_ids={1})
    assert reason is not None
    assert str(MIN_FINDINGS_REFERENCED) in reason


def test_violation_flags_zero_complaint_findings() -> None:
    reason = _violation([2, 3, 4], all_ids={1, 2, 3, 4}, complaint_ids={1})
    assert reason is not None
    assert "pain_point" in reason


def test_violation_deduplicates_cited_ids_before_counting() -> None:
    # Three citations, but only two distinct ids -> still under the minimum.
    reason = _violation([1, 1, 2], all_ids={1, 2, 3}, complaint_ids={1})
    assert reason is not None


# ---------------------------------------------------------------------------
# _can_possibly_satisfy — the short-circuit guard
# ---------------------------------------------------------------------------


def test_cannot_satisfy_with_fewer_than_three_findings() -> None:
    findings = [_finding(1, PAIN), _finding(2, PAIN)]
    assert _can_possibly_satisfy(findings) is False


def test_cannot_satisfy_with_zero_complaint_findings() -> None:
    findings = [_finding(1, COMPETITOR), _finding(2, COMPETITOR), _finding(3, COMPETITOR)]
    assert _can_possibly_satisfy(findings) is False


def test_can_satisfy_with_three_findings_including_one_complaint() -> None:
    findings = [_finding(1, PAIN), _finding(2, COMPETITOR), _finding(3, COMPETITOR)]
    assert _can_possibly_satisfy(findings) is True


@pytest.mark.parametrize("generator", [generate_mvp, generate_feature_gaps, generate_risks])
async def test_short_circuit_makes_no_llm_call_when_guard_cannot_pass(generator) -> None:  # type: ignore[no-untyped-def]
    """`ctx=None` would blow up the moment `structured()` tried to use it —
    if any of these functions makes an LLM call despite an unsatisfiable
    finding set, this test fails with an AttributeError, not a false pass."""
    findings = [_finding(1, COMPETITOR), _finding(2, COMPETITOR)]
    result = await generator(findings, ctx=None)  # type: ignore[arg-type]
    assert result is None


async def test_short_circuit_fires_on_zero_complaints_even_with_enough_findings() -> None:
    findings = [_finding(i, COMPETITOR) for i in range(1, 6)]
    result = await generate_mvp(findings, ctx=None)  # type: ignore[arg-type]
    assert result is None
