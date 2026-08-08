"""Phase 14: load and validate `bench/queries/*.yaml` against the masterplan
§10 schema. Two disciplines the phase doc calls out as "easy to skip and
expensive to skip" are enforced mechanically here, not left as convention:

1. **Ground truth decays.** A fact older than `STALENESS_DAYS` is unusable —
   `BenchmarkQuery.facts_as_of(today)` raises rather than silently scoring
   against a stale number.
2. **Six tuning / four held-out, touched once.** `load_held_out_queries`
   requires an explicit `confirm=True` and always logs a loud warning, so
   accidentally peeking during calibration is structurally awkward rather
   than merely discouraged.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import structlog
import yaml
from pydantic import BaseModel, Field, field_validator

logger = structlog.get_logger()

QUERIES_DIR = Path(__file__).parent / "queries"

STALENESS_DAYS = 60

Difficulty = Literal["easy", "medium", "hard"]
Split = Literal["tuning", "held_out"]


class GroundTruthFact(BaseModel):
    entity: str
    attribute: str
    value: float | str | bool
    verified_on: date
    source: str | None = None


class GroundTruth(BaseModel):
    must_include: list[str] = Field(default_factory=list)
    known_absent: list[str] = Field(default_factory=list)
    facts: list[GroundTruthFact] = Field(default_factory=list)


class BenchmarkQuery(BaseModel):
    id: str
    query: str
    difficulty: Difficulty
    split: Split
    role: str | None = None
    ground_truth: GroundTruth

    @field_validator("id")
    @classmethod
    def _id_looks_like_qnn(cls, v: str) -> str:
        if not (v.startswith("q") and v[1:].isdigit()):
            raise ValueError(f"query id {v!r} does not match the q<NN> convention")
        return v

    def stale_facts(
        self, *, as_of: date, staleness_days: int = STALENESS_DAYS
    ) -> list[GroundTruthFact]:
        """Facts whose `verified_on` is more than `staleness_days` before
        `as_of` — masterplan §10: "A `verified_on` older than ~60 days makes
        the fact unusable until re-checked"."""
        return [
            fact
            for fact in self.ground_truth.facts
            if (as_of - fact.verified_on).days > staleness_days
        ]


class StaleGroundTruthError(Exception):
    """Raised by `load_query`/`load_tuning_queries`/`load_held_out_queries`
    when any fact's `verified_on` has aged past `STALENESS_DAYS` — the
    runner refuses to score against it rather than quietly reporting a wrong
    number (phase doc: "enforced by the runner, which refuses to score
    against stale ground truth")."""

    def __init__(self, query_id: str, stale: list[GroundTruthFact]) -> None:
        self.query_id = query_id
        self.stale = stale
        entities = ", ".join(f"{f.entity}/{f.attribute}" for f in stale)
        super().__init__(
            f"{query_id}: {len(stale)} fact(s) older than {STALENESS_DAYS} days, "
            f"re-verify before scoring: {entities}"
        )


class HeldOutAccessError(Exception):
    """Raised by `load_held_out_queries` when called without `confirm=True`
    — the mechanical half of "touched once, at the end" (masterplan §10);
    the loud log line on every successful call is the other half."""


def _read_yaml(path: Path) -> BenchmarkQuery:
    with path.open() as f:
        raw = yaml.safe_load(f)
    return BenchmarkQuery.model_validate(raw)


def load_all_queries(*, queries_dir: Path = QUERIES_DIR) -> list[BenchmarkQuery]:
    """Every `q*.yaml` in `queries_dir`, sorted by id. No staleness check,
    no split filtering — the one place that reads the raw files, everything
    else layers on top of this."""
    paths = sorted(queries_dir.glob("q*.yaml"))
    return [_read_yaml(p) for p in paths]


def _check_fresh(query: BenchmarkQuery, *, as_of: date) -> BenchmarkQuery:
    stale = query.stale_facts(as_of=as_of)
    if stale:
        raise StaleGroundTruthError(query.id, stale)
    return query


def load_tuning_queries(
    *, as_of: date | None = None, queries_dir: Path = QUERIES_DIR
) -> list[BenchmarkQuery]:
    """The six tuning queries, staleness-checked. This is the default,
    always-accessible path — calibration happens against these."""
    today = as_of or date.today()
    return [
        _check_fresh(q, as_of=today)
        for q in load_all_queries(queries_dir=queries_dir)
        if q.split == "tuning"
    ]


def load_held_out_queries(
    *, confirm: bool, as_of: date | None = None, queries_dir: Path = QUERIES_DIR
) -> list[BenchmarkQuery]:
    """The four held-out queries. `confirm=True` is mandatory — this is not
    a style preference, it is the "inaccessible during tuning mode" exit
    criterion made mechanical: a caller has to affirmatively opt in, and
    every successful call is logged loudly so a held-out run never happens
    silently."""
    if not confirm:
        raise HeldOutAccessError(
            "load_held_out_queries requires confirm=True — held-out queries are touched "
            "once, at the end of calibration, never during tuning (masterplan §10)"
        )
    logger.warning(
        "bench.held_out_queries_accessed",
        message="held-out queries loaded — this should happen once, after tuning is final",
    )
    today = as_of or date.today()
    return [
        _check_fresh(q, as_of=today)
        for q in load_all_queries(queries_dir=queries_dir)
        if q.split == "held_out"
    ]


__all__ = [
    "STALENESS_DAYS",
    "BenchmarkQuery",
    "GroundTruth",
    "GroundTruthFact",
    "HeldOutAccessError",
    "StaleGroundTruthError",
    "load_all_queries",
    "load_held_out_queries",
    "load_tuning_queries",
]
