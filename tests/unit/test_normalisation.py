from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from api.retrieval.extract_text import normalise


def test_crlf_and_cr_become_lf() -> None:
    assert normalise("a\r\nb\rc\n") == "a\nb\nc\n"


def test_collapses_three_or_more_blank_lines_to_two() -> None:
    assert normalise("a\n\n\n\n\nb\n") == "a\n\nb\n"


def test_strips_trailing_whitespace_per_line() -> None:
    assert normalise("a   \nb\t\n") == "a\nb\n"


def test_ensures_single_trailing_newline() -> None:
    assert normalise("a\nb") == "a\nb\n"
    assert normalise("a\nb\n\n\n\n") == "a\nb\n"


def test_nfc_normalises_unicode() -> None:
    decomposed = "é"  # "e" + combining acute accent
    composed = "é"  # "é"
    assert normalise(decomposed) == normalise(composed)


def test_ascii_offsets_preserved_when_no_normalisation_needed() -> None:
    # Span binding (Phase 06) depends on this: text that is already in
    # canonical form must survive untouched, byte-for-byte, so char offsets
    # computed against it stay valid.
    text = "Pricing starts at $10/month for the Pro plan.\n"
    assert normalise(text) == text


@given(text=st.text(max_size=200))
def test_normalise_is_idempotent(text: str) -> None:
    once = normalise(text)
    twice = normalise(once)
    assert once == twice
