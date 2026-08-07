"""The most important test file in the repo (phase doc). `bind_span` is the
single mechanism the "every sentence binds to a span" guarantee rests on —
these tests assert its exact behaviour, including the edge cases a naive
`str.find` gets wrong.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from api.extract.span import CONTEXT_RADIUS, Span, bind_span, quote_context_window

_DIGITS = st.text(alphabet="0123456789", max_size=30)
_LETTERS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=12)

# ---------------------------------------------------------------------------
# bind_span — table-driven cases from the phase doc's test spec
# ---------------------------------------------------------------------------


def test_exact_match_at_start() -> None:
    assert bind_span("hello world", "hello") == Span(0, 5)


def test_exact_match_at_end() -> None:
    assert bind_span("hello world", "world") == Span(6, 11)


def test_exact_match_in_middle() -> None:
    assert bind_span("the quick brown fox", "quick brown") == Span(4, 15)


def test_quote_absent_returns_none() -> None:
    assert bind_span("hello world", "goodbye") is None


def test_quote_present_twice_returns_none() -> None:
    assert bind_span("abc abc", "abc") is None


def test_quote_present_three_times_returns_none() -> None:
    assert bind_span("x abc y abc z abc", "abc") is None


def test_quote_differing_by_one_character_returns_none() -> None:
    assert bind_span("The price is $10/month.", "The price is $11/month.") is None


def test_quote_differing_only_in_whitespace_returns_none() -> None:
    assert bind_span("The  price is $10", "The price is $10") is None


def test_quote_differing_only_in_unicode_normalisation_form_returns_none() -> None:
    decomposed = "é"  # "e" + combining acute accent
    composed = "é"  # "é", precomposed
    text = f"caf{decomposed} menu"
    assert bind_span(text, f"caf{composed}") is None


def test_empty_quote_returns_none() -> None:
    assert bind_span("hello world", "") is None


def test_empty_quote_against_empty_source_returns_none() -> None:
    assert bind_span("", "") is None


def test_quote_longer_than_source_returns_none() -> None:
    assert bind_span("short", "this is definitely longer than short") is None


def test_multibyte_characters_offsets_are_code_point_indices() -> None:
    text = "price: \U0001f600 $5/mo 中文 done"
    quote = "$5/mo 中文"
    span = bind_span(text, quote)
    assert span is not None
    assert text[span.start : span.end] == quote


def test_emoji_before_quote_does_not_shift_offset_semantics() -> None:
    # The emoji is a surrogate pair in JS/UTF-16 but one Python code point;
    # Phase 13 must consume these as code-point indices, not UTF-16 units.
    text = "\U0001f600 the price is $5/mo here"
    quote = "$5/mo"
    span = bind_span(text, quote)
    assert span is not None
    assert text[span.start : span.end] == quote


# ---------------------------------------------------------------------------
# bind_span — the two property tests that matter (phase doc)
# ---------------------------------------------------------------------------


@given(prefix=_DIGITS, needle=_LETTERS, suffix=_DIGITS)
def test_bind_span_roundtrips_for_a_uniquely_occurring_substring(
    prefix: str, needle: str, suffix: str
) -> None:
    text = prefix + needle + suffix
    span = bind_span(text, needle)
    assert span is not None
    assert text[span.start : span.end] == needle


@given(text=_DIGITS, needle=_LETTERS)
def test_bind_span_never_false_positive_when_quote_absent(text: str, needle: str) -> None:
    assert bind_span(text, needle) is None


# ---------------------------------------------------------------------------
# quote_context_window
# ---------------------------------------------------------------------------


def test_context_window_basic_slice_and_offset() -> None:
    text = "a" * 5000 + "TARGET" + "b" * 5000
    span = Span(start=5000, end=5006)
    ctx, offset = quote_context_window(text, span)
    assert ctx[offset : offset + 6] == "TARGET"
    assert len(ctx) == 2 * CONTEXT_RADIUS + 6


def test_context_window_clamped_at_start_of_text() -> None:
    text = "TARGET" + "x" * 100
    span = Span(start=0, end=6)
    ctx, offset = quote_context_window(text, span)
    assert offset == 0
    assert ctx.startswith("TARGET")


def test_context_window_clamped_at_end_of_text() -> None:
    text = "x" * 100 + "TARGET"
    span = Span(start=100, end=106)
    ctx, offset = quote_context_window(text, span)
    assert ctx[offset : offset + 6] == "TARGET"
    assert ctx.endswith("TARGET")


def test_context_window_custom_radius() -> None:
    text = "a" * 100 + "TARGET" + "b" * 100
    span = Span(start=100, end=106)
    ctx, offset = quote_context_window(text, span, radius=10)
    assert ctx == "a" * 10 + "TARGET" + "b" * 10
    assert offset == 10


@given(
    prefix=_DIGITS, needle=_LETTERS, suffix=_DIGITS, radius=st.integers(min_value=0, max_value=50)
)
def test_context_window_agrees_with_source_text(
    prefix: str, needle: str, suffix: str, radius: int
) -> None:
    text = prefix + needle + suffix
    span = bind_span(text, needle)
    assert span is not None
    ctx, offset = quote_context_window(text, span, radius=radius)
    assert text[span.start : span.end] == ctx[offset : offset + len(needle)]
