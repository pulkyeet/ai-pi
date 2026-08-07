"""The masterplan §4.1 `TASKS` registry, plus the planning-time constants
that fall out of it (Phase 09 phase doc).

`TaskKind`, `TASK_ARGS` and `TASK_COST_WEIGHT` are already frozen Phase 00
contracts (`api.models.plan`) — this module does not redeclare them, it adds
the one thing Phase 00 deliberately left out: which of those seven kinds a
*planner* can actually put in a seed DAG.

**Seed-plannable vs runtime-spawned.** `profile_product`, `extract_pricing`,
`oss_profile` and `find_funding` all require an `entity_key` (or `repo`) —
values that do not exist yet when Stage 1 runs, because no entity has been
discovered. The masterplan says this outright: "the DAG is a *seed*:
`discover_competitors` does not know entity keys yet, so it emits
`profile_product` children dynamically at runtime via `HandlerResult`"
(phase doc, "Design > Stage 1"). That reasoning applies identically to the
other three entity-keyed kinds. So the planner can only ever emit
`SEED_KINDS` below; the rest are always spawned later by a Phase 10 task
handler, never chosen here. `api.planner.validate` treats any other kind
appearing in a seed `Plan` as a bad-args-class domain error (there is no
legitimate value it could have put in `entity_key`/`repo` yet).

`DEFAULT_RUN_BUDGET_WEIGHT` is the masterplan §2 output-contract example's
own `total_budget_weight: 40` — a concrete anchor to fall back on until
Phase 14 measures a real number and `Settings.run_budget_weight` stops being
`None` (masterplan §8.2: quota knobs stay `TBD` until then). Same story for
`DEFAULT_MAX_COMPETITORS_PROFILED`: `Settings.max_competitors_profiled` is
`None` today, so callers that don't have a Phase 14 number yet fall back to
this constant rather than the planner being unable to bound its own fan-out.
Both are ordinary function parameters with these as defaults — never read
from `api.config` directly, matching `api.llm.gateway.build_context`'s own
rule that gateway-adjacent modules take plain values so they stay free of a
`Settings` dependency.
"""

from __future__ import annotations

from api.models.plan import TASK_ARGS, TASK_COST_WEIGHT, TaskKind

SEED_KINDS: frozenset[TaskKind] = frozenset(
    {TaskKind.DISCOVER_COMPETITORS, TaskKind.MINE_COMMUNITY, TaskKind.TREND_SIGNALS}
)

RUNTIME_SPAWNED_KINDS: frozenset[TaskKind] = frozenset(TaskKind) - SEED_KINDS

DEFAULT_RUN_BUDGET_WEIGHT = 40
DEFAULT_MAX_COMPETITORS_PROFILED = 8

__all__ = [
    "DEFAULT_MAX_COMPETITORS_PROFILED",
    "DEFAULT_RUN_BUDGET_WEIGHT",
    "RUNTIME_SPAWNED_KINDS",
    "SEED_KINDS",
    "TASK_ARGS",
    "TASK_COST_WEIGHT",
    "TaskKind",
]
