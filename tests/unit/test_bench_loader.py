"""`bench.loader` (Phase 14): YAML -> validated `BenchmarkQuery`, the
staleness gate, and the tuning/held-out mechanical split. No Postgres
needed — pure file I/O + Pydantic.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml
from bench.loader import (
    STALENESS_DAYS,
    HeldOutAccessError,
    StaleGroundTruthError,
    load_all_queries,
    load_held_out_queries,
    load_tuning_queries,
)
from pydantic import ValidationError

TODAY = date(2026, 8, 8)


def _write_query(
    tmp_path: Path,
    *,
    id: str = "q01",
    split: str = "tuning",
    verified_on: date = TODAY,
    difficulty: str = "easy",
) -> Path:
    payload = {
        "id": id,
        "query": "project management tool",
        "difficulty": difficulty,
        "split": split,
        "ground_truth": {
            "must_include": ["asana.com", "trello.com"],
            "known_absent": ["stripe.com"],
            "facts": [
                {
                    "entity": "asana.com",
                    "attribute": "pricing.entry_usd_month",
                    "value": 10.99,
                    "verified_on": verified_on.isoformat(),
                }
            ],
        },
    }
    path = tmp_path / f"{id}.yaml"
    path.write_text(yaml.safe_dump(payload))
    return path


def test_load_all_queries_parses_a_valid_yaml(tmp_path: Path) -> None:
    _write_query(tmp_path)
    queries = load_all_queries(queries_dir=tmp_path)
    assert len(queries) == 1
    q = queries[0]
    assert q.id == "q01"
    assert q.query == "project management tool"
    assert q.ground_truth.must_include == ["asana.com", "trello.com"]
    assert q.ground_truth.known_absent == ["stripe.com"]
    assert q.ground_truth.facts[0].value == pytest.approx(10.99)


def test_load_all_queries_sorted_by_id(tmp_path: Path) -> None:
    _write_query(tmp_path, id="q10")
    _write_query(tmp_path, id="q02")
    queries = load_all_queries(queries_dir=tmp_path)
    assert [q.id for q in queries] == ["q02", "q10"]


def test_id_must_match_q_nn_convention(tmp_path: Path) -> None:
    _write_query(tmp_path, id="query-one")
    with pytest.raises(ValidationError):
        load_all_queries(queries_dir=tmp_path)


# ---------------------------------------------------------------------------
# staleness gate
# ---------------------------------------------------------------------------


def test_fresh_fact_within_staleness_window_loads_fine(tmp_path: Path) -> None:
    verified = TODAY.fromordinal(TODAY.toordinal() - STALENESS_DAYS)  # exactly at the boundary
    _write_query(tmp_path, verified_on=verified)
    queries = load_tuning_queries(as_of=TODAY, queries_dir=tmp_path)
    assert len(queries) == 1


def test_stale_fact_past_the_window_is_refused(tmp_path: Path) -> None:
    verified = TODAY.fromordinal(TODAY.toordinal() - STALENESS_DAYS - 1)
    _write_query(tmp_path, verified_on=verified)
    with pytest.raises(StaleGroundTruthError) as exc_info:
        load_tuning_queries(as_of=TODAY, queries_dir=tmp_path)
    assert "q01" in str(exc_info.value)


def test_load_all_queries_itself_does_not_check_staleness(tmp_path: Path) -> None:
    # `load_all_queries` is the raw read; only the split-aware loaders
    # (`load_tuning_queries`/`load_held_out_queries`) enforce the discipline.
    verified = date(2020, 1, 1)
    _write_query(tmp_path, verified_on=verified)
    queries = load_all_queries(queries_dir=tmp_path)
    assert len(queries) == 1


# ---------------------------------------------------------------------------
# tuning / held-out split
# ---------------------------------------------------------------------------


def test_load_tuning_queries_only_returns_tuning_split(tmp_path: Path) -> None:
    _write_query(tmp_path, id="q01", split="tuning")
    _write_query(tmp_path, id="q02", split="held_out")
    tuning = load_tuning_queries(as_of=TODAY, queries_dir=tmp_path)
    assert [q.id for q in tuning] == ["q01"]


def test_load_held_out_queries_without_confirm_raises(tmp_path: Path) -> None:
    _write_query(tmp_path, id="q02", split="held_out")
    with pytest.raises(HeldOutAccessError):
        load_held_out_queries(confirm=False, as_of=TODAY, queries_dir=tmp_path)


def test_load_held_out_queries_with_confirm_returns_held_out_split(tmp_path: Path) -> None:
    _write_query(tmp_path, id="q01", split="tuning")
    _write_query(tmp_path, id="q02", split="held_out")
    held_out = load_held_out_queries(confirm=True, as_of=TODAY, queries_dir=tmp_path)
    assert [q.id for q in held_out] == ["q02"]


def test_load_held_out_queries_still_enforces_staleness(tmp_path: Path) -> None:
    stale = date(2020, 1, 1)
    _write_query(tmp_path, id="q02", split="held_out", verified_on=stale)
    with pytest.raises(StaleGroundTruthError):
        load_held_out_queries(confirm=True, as_of=TODAY, queries_dir=tmp_path)
