"""`api.synth.bind` — sentence segmentation and per-sentence citation
binding, entirely offline (no Postgres, no LLM). This is the phase's
signature mechanism in miniature: split, resolve markers, drop what doesn't
resolve, strip markers from what survives."""

from __future__ import annotations

from api.synth.bind import bind_many, bind_statement, split_sentences
from api.synth.findings import Finding, FindingKind


def _finding(finding_id: int, claim_ids: list[int]) -> Finding:
    return Finding(
        id=finding_id,
        run_id="r1",
        kind=FindingKind.PAIN_POINT,
        statement="x",
        claim_ids=claim_ids,
    )


FINDINGS = {
    1: _finding(1, [10, 11]),
    2: _finding(2, [20]),
    3: _finding(3, [30, 31, 32]),
}


# ---------------------------------------------------------------------------
# sentence splitting — the phase doc's own named trap cases
# ---------------------------------------------------------------------------


def test_split_does_not_break_on_currency_amounts() -> None:
    sentences = split_sentences("Pricing starts at $5.00/mo. It rises after year one.")
    assert len(sentences) == 2
    assert sentences[0].startswith("Pricing starts at $5.00/mo.")


def test_split_does_not_break_on_eg_abbreviation() -> None:
    sentences = split_sentences("Several competitors, e.g. Acme and Beta, ship this. It is common.")
    assert len(sentences) == 2


def test_split_does_not_break_on_inc_abbreviation() -> None:
    sentences = split_sentences("Acme Inc. raised a seed round. It has ten employees.")
    assert len(sentences) == 2


def test_split_does_not_break_on_ellipsis() -> None:
    sentences = split_sentences("Users kept asking for more... eventually we listened. Done.")
    assert len(sentences) == 2


def test_split_handles_multiple_ordinary_sentences() -> None:
    sentences = split_sentences("First sentence. Second sentence. Third sentence.")
    assert len(sentences) == 3


def test_split_ignores_blank_input() -> None:
    assert split_sentences("") == []
    assert split_sentences("   ") == []


# ---------------------------------------------------------------------------
# bind_statement — sentence-level drop/keep
# ---------------------------------------------------------------------------


def test_sentence_with_valid_marker_is_kept_and_marker_stripped() -> None:
    result = bind_statement("Users struggle with manual entry [1].", findings_by_id=FINDINGS)
    assert result is not None
    assert result.statement == "Users struggle with manual entry."
    assert "[1]" not in result.statement
    assert result.addresses_finding_ids == (1,)
    assert set(result.claim_ids) == {10, 11}


def test_sentence_with_no_marker_is_dropped() -> None:
    text = "This sentence cites a finding [1]. This one cites nothing at all."
    result = bind_statement(text, findings_by_id=FINDINGS)
    assert result is not None
    assert result.statement == "This sentence cites a finding."
    assert "cites nothing" not in result.statement


def test_sentence_with_unknown_finding_id_is_dropped() -> None:
    text = "This cites a real finding [1]. This cites a fabricated one [999]."
    result = bind_statement(text, findings_by_id=FINDINGS)
    assert result is not None
    assert result.addresses_finding_ids == (1,)


def test_sentence_with_multiple_ids_in_one_marker_binds_all() -> None:
    result = bind_statement("Two findings support this claim [1, 2].", findings_by_id=FINDINGS)
    assert result is not None
    assert set(result.addresses_finding_ids) == {1, 2}
    assert set(result.claim_ids) == {10, 11, 20}


def test_section_emptied_by_drops_returns_none_not_empty_string() -> None:
    """The phase doc's own instruction: an emptied section is omitted, not
    returned as an empty-string statement."""
    result = bind_statement("Nothing here is cited at all.", findings_by_id={})
    assert result is None


def test_duplicate_finding_ids_across_sentences_are_deduplicated() -> None:
    text = "First point from finding one [1]. Second point, same finding [1]."
    result = bind_statement(text, findings_by_id=FINDINGS)
    assert result is not None
    assert result.addresses_finding_ids == (1,)
    assert result.claim_ids == (10, 11)


def test_empty_text_binds_to_none() -> None:
    assert bind_statement("", findings_by_id=FINDINGS) is None


# ---------------------------------------------------------------------------
# bind_many — one item per generated feature-gap/risk entry
# ---------------------------------------------------------------------------


def test_bind_many_drops_only_unbindable_items() -> None:
    texts = [
        "A well-cited gap [1].",
        "An uncited gap with no marker at all.",
        "Another well-cited gap [2].",
    ]
    bound = bind_many(texts, findings_by_id=FINDINGS)
    assert len(bound) == 2
    assert {b.addresses_finding_ids for b in bound} == {(1,), (2,)}


def test_bind_many_with_everything_uncited_returns_empty_list() -> None:
    assert bind_many(["nothing cited"], findings_by_id=FINDINGS) == []


def test_bind_many_empty_input_returns_empty_list() -> None:
    assert bind_many([], findings_by_id=FINDINGS) == []
