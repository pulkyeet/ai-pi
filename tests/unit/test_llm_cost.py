from __future__ import annotations

import pytest

from api.llm.cost import MODEL_RATES, ModelRate, UnknownModelError, compute_cost_usd


def test_compute_cost_usd_matches_manual_arithmetic() -> None:
    rate = MODEL_RATES["deepseek/deepseek-v4-flash"]
    cost = compute_cost_usd(
        "deepseek/deepseek-v4-flash",
        input_tokens=1_000_000,
        output_tokens=500_000,
        cached_tokens=0,
    )
    expected = (
        1_000_000 * rate.input_usd_per_m / 1_000_000 + 500_000 * rate.output_usd_per_m / 1_000_000
    )
    assert cost == pytest.approx(expected)


def test_cached_tokens_are_a_subset_of_input_priced_separately() -> None:
    # 1000 input tokens, 400 of which were a cache read: 600 fresh @ input rate,
    # 400 @ the cheaper cached-read rate — not 1000 @ input rate plus 400 more.
    cost = compute_cost_usd(
        "deepseek/deepseek-v4-flash", input_tokens=1000, output_tokens=0, cached_tokens=400
    )
    rate = MODEL_RATES["deepseek/deepseek-v4-flash"]
    expected = (600 * rate.input_usd_per_m + 400 * rate.cached_input_usd_per_m) / 1_000_000
    assert cost == pytest.approx(expected)


def test_zero_tokens_costs_nothing() -> None:
    assert (
        compute_cost_usd(
            "deepseek/deepseek-v4-flash", input_tokens=0, output_tokens=0, cached_tokens=0
        )
        == 0.0
    )


def test_unknown_model_raises() -> None:
    with pytest.raises(UnknownModelError):
        compute_cost_usd("not-a-real-model", input_tokens=10, output_tokens=10, cached_tokens=0)


def test_reprice_is_a_config_edit_not_a_code_change() -> None:
    """Cost config change alters computed cost with no code change (phase
    doc's own test spec) — proven by passing a custom rate table rather
    than editing `compute_cost_usd` itself."""
    custom_rates = {
        "toy-model": ModelRate(
            input_usd_per_m=1.0, output_usd_per_m=2.0, cached_input_usd_per_m=0.5
        )
    }
    cost = compute_cost_usd(
        "toy-model",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cached_tokens=0,
        rates=custom_rates,
    )
    assert cost == pytest.approx(3.0)
