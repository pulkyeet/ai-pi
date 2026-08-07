"""Coverage scoring (masterplan Rule 4, §2) — reported separately from
confidence:

> A run whose funding branch died says so, out loud, on the report.

Computed from the actual DAG's outcomes and weighted by the planner's own
`cost_weight`, never self-reported (Risks table: a run that plans little
cannot score high coverage, since planned weight is the denominator). A
"branch" is one `TaskKind` — it is dead only if *every* task of that kind
failed or was skipped, not merely one of several (e.g. several
`profile_product` tasks, one per competitor); failed vs. skipped-for-budget
are kept distinct so the report can say which happened, per the phase
doc's own insistence that the distinction "survive to the surface".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TaskStatus = Literal["done", "failed", "skipped"]


@dataclass(frozen=True)
class TaskOutcome:
    kind: str
    cost_weight: int
    status: TaskStatus
    # Only meaningful when status == "skipped", e.g. "budget" (the
    # executor's own dispatch-loop skip reason, masterplan §4.2).
    skip_reason: str | None = None


@dataclass(frozen=True)
class CoverageResult:
    score: float
    failed_branches: tuple[str, ...]
    budget_skipped_branches: tuple[str, ...]
    other_skipped_branches: tuple[str, ...]


def compute_coverage(
    tasks: list[TaskOutcome],
    *,
    total_entities: int = 0,
    insufficient_signal_entities: int = 0,
) -> CoverageResult:
    """`total_entities`/`insufficient_signal_entities` fold in Phase 07's
    own contributing signal: a run that found competitors but could not
    classify any of them (`MaturityAssignment.insufficient_signal`) reports
    reduced coverage rather than false confidence, even if every planned
    task otherwise completed.
    """
    if insufficient_signal_entities < 0 or insufficient_signal_entities > total_entities:
        raise ValueError("insufficient_signal_entities must be between 0 and total_entities")

    planned_weight = sum(t.cost_weight for t in tasks)
    completed_weight = sum(t.cost_weight for t in tasks if t.status == "done")
    task_score = completed_weight / planned_weight if planned_weight else 1.0

    by_kind: dict[str, list[TaskOutcome]] = {}
    for t in tasks:
        by_kind.setdefault(t.kind, []).append(t)

    failed_branches = []
    budget_skipped_branches = []
    other_skipped_branches = []
    for kind, kind_tasks in sorted(by_kind.items()):
        if any(t.status == "done" for t in kind_tasks):
            continue  # not a dead branch: at least one task of this kind succeeded
        if any(t.status == "failed" for t in kind_tasks):
            failed_branches.append(kind)
        elif all(t.skip_reason == "budget" for t in kind_tasks):
            budget_skipped_branches.append(kind)
        else:
            other_skipped_branches.append(kind)

    entity_score = 1 - (insufficient_signal_entities / total_entities) if total_entities else 1.0
    score = task_score * entity_score

    return CoverageResult(
        score=score,
        failed_branches=tuple(failed_branches),
        budget_skipped_branches=tuple(budget_skipped_branches),
        other_skipped_branches=tuple(other_skipped_branches),
    )


__all__ = ["CoverageResult", "TaskOutcome", "compute_coverage"]
