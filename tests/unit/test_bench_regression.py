"""`bench.regression` (Phase 14): the CI nightly failure conditions
(masterplan-derived phase doc: sentence binding < 100%, recall drop > 10
points, cost increase > 50%, contradiction firing hits zero on the trap).
Pure comparison logic over synthetic `QueryScore`s — no Postgres, no files
beyond what `tmp_path` gives each test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bench.regression import (
    TRAP_QUERY_ID,
    check_regression,
    load_baseline,
    load_latest_results,
)
from bench.runner import QueryScore


def _score(
    query_id: str = "q01",
    *,
    competitor_recall: float = 1.0,
    sentence_binding_rate: float = 1.0,
    cost_usd: float = 0.01,
    contradiction_fired: bool = False,
) -> QueryScore:
    return QueryScore(
        query_id=query_id,
        query="project management tool",
        run_id="r_test",
        difficulty="easy",
        split="tuning",
        competitor_recall=competitor_recall,
        precision_proxy=1.0,
        fact_accuracy=1.0,
        sentence_binding_rate=sentence_binding_rate,
        contradiction_fired=contradiction_fired,
        cost_usd=cost_usd,
        llm_cost_usd=cost_usd / 2,
        search_cost_usd=cost_usd / 2,
        duration_s=100.0,
        coverage=1.0,
        used_fallback=False,
        claims_dropped={},
        synthesis_omitted_sections=[],
    )


def test_no_failures_when_matching_baseline() -> None:
    baseline = {"q01": _score()}
    current = {"q01": _score()}
    assert check_regression(current, baseline) == []


def test_sentence_binding_below_one_always_fails_even_without_baseline() -> None:
    current = {"q01": _score(sentence_binding_rate=0.9)}
    failures = check_regression(current, baseline={})
    assert len(failures) == 1
    assert "sentence_binding_rate" in failures[0]


def test_recall_drop_within_tolerance_passes() -> None:
    baseline = {"q01": _score(competitor_recall=0.8)}
    current = {"q01": _score(competitor_recall=0.71)}  # 9pt drop, under the 10pt tolerance
    assert check_regression(current, baseline) == []


def test_recall_drop_beyond_tolerance_fails() -> None:
    baseline = {"q01": _score(competitor_recall=0.8)}
    current = {"q01": _score(competitor_recall=0.65)}  # 15pt drop
    failures = check_regression(current, baseline)
    assert len(failures) == 1
    assert "competitor_recall dropped" in failures[0]


def test_cost_increase_within_tolerance_passes() -> None:
    baseline = {"q01": _score(cost_usd=0.01)}
    current = {"q01": _score(cost_usd=0.014)}  # +40%, under the 50% tolerance
    assert check_regression(current, baseline) == []


def test_cost_increase_beyond_tolerance_fails() -> None:
    baseline = {"q01": _score(cost_usd=0.01)}
    current = {"q01": _score(cost_usd=0.02)}  # +100%
    failures = check_regression(current, baseline)
    assert len(failures) == 1
    assert "cost_usd rose" in failures[0]


def test_trap_query_contradiction_stops_firing_fails() -> None:
    baseline = {TRAP_QUERY_ID: _score(TRAP_QUERY_ID, contradiction_fired=True)}
    current = {TRAP_QUERY_ID: _score(TRAP_QUERY_ID, contradiction_fired=False)}
    failures = check_regression(current, baseline)
    assert len(failures) == 1
    assert "contradiction_fired is False" in failures[0]


def test_trap_query_still_firing_passes() -> None:
    baseline = {TRAP_QUERY_ID: _score(TRAP_QUERY_ID, contradiction_fired=True)}
    current = {TRAP_QUERY_ID: _score(TRAP_QUERY_ID, contradiction_fired=True)}
    assert check_regression(current, baseline) == []


def test_new_query_with_no_baseline_entry_is_not_a_failure() -> None:
    current = {"q11": _score("q11")}
    assert check_regression(current, baseline={}) == []


def test_load_latest_results_picks_the_most_recent_dated_dir(tmp_path: Path) -> None:
    for day in ("2026-08-01", "2026-08-08", "2026-07-15"):
        d = tmp_path / day
        d.mkdir()
        score = _score()
        (d / "q01.json").write_text(json.dumps(_asdict_via_score(score)))
    scores = load_latest_results(results_dir=tmp_path)
    assert scores["q01"].query_id == "q01"


def test_load_latest_results_raises_when_no_dated_dirs(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_latest_results(results_dir=tmp_path)


def test_load_baseline_raises_when_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_baseline(baseline_path=tmp_path / "nope.json")


def test_load_baseline_round_trips(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps({"q01": _asdict_via_score(_score())}))
    loaded = load_baseline(baseline_path=baseline_path)
    assert loaded["q01"].query_id == "q01"


def _asdict_via_score(score: QueryScore) -> dict[str, object]:
    from dataclasses import asdict

    return asdict(score)
