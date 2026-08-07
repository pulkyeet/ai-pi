from __future__ import annotations

from api.llm.client import RawCompletion, _parse_completion, combine_usage


def test_parse_completion_reads_usage_and_cached_tokens() -> None:
    body = {
        "choices": [{"message": {"content": '{"a": 1}'}}],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 80},
        },
    }
    raw = _parse_completion(body)
    assert raw.content == '{"a": 1}'
    assert raw.input_tokens == 120
    assert raw.output_tokens == 30
    assert raw.cached_tokens == 80


def test_parse_completion_defaults_missing_usage_to_zero() -> None:
    body = {"choices": [{"message": {"content": "{}"}}]}
    raw = _parse_completion(body)
    assert raw.input_tokens == 0
    assert raw.output_tokens == 0
    assert raw.cached_tokens == 0


def test_combine_usage_sums_both_calls_keeps_second_content() -> None:
    first = RawCompletion(content="bad", input_tokens=100, output_tokens=20, cached_tokens=10)
    second = RawCompletion(content="good", input_tokens=110, output_tokens=15, cached_tokens=0)
    combined = combine_usage(first, second)
    assert combined.content == "good"
    assert combined.input_tokens == 210
    assert combined.output_tokens == 35
    assert combined.cached_tokens == 10
