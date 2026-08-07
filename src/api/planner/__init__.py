"""Interpreter & Planner (Phase 09, masterplan §3/§4.1).

Stage 0 and Stage 1 of the masterplan's system flow: understand what was
asked, then decide what research to do about it.

```
api.planner.interpret   Stage 0 — free text -> ResearchBrief + keywords,
                         input validation, the disambiguation decision
api.planner.plan        Stage 1 — brief -> schema+domain-valid seed Plan,
                         with one domain-level repair round
api.planner.registry    the fixed TASKS registry, seed-plannable kinds,
                         and the constants Stage 1 falls back to pre-Phase14
api.planner.validate    DAG domain validation (bad args, over budget,
                         missing discovery) not already covered by
                         api.models.plan.Plan's own invariants
api.planner.fallback    the deterministic default plan — a run never fails
                         because planning failed
```

Like `api.evidence`, this phase has no single orchestrating entry point:
Stage 0 and Stage 1 run at different points in a run's lifecycle (Stage 0
before a disambiguation round-trip that is out of this phase's scope —
Phase 12/13; Stage 1 only once the brief is final), so each stage is called
independently by whatever wires the full run together (Phase 10).
"""

from __future__ import annotations

from api.planner import fallback, interpret, plan, registry, validate
from api.planner.interpret import InterpretResult, QueryRejectedError, RejectionReason
from api.planner.interpret import interpret as run_interpret
from api.planner.plan import PlanOutcome
from api.planner.plan import plan_stage1 as run_plan

__all__ = [
    "InterpretResult",
    "PlanOutcome",
    "QueryRejectedError",
    "RejectionReason",
    "fallback",
    "interpret",
    "plan",
    "registry",
    "run_interpret",
    "run_plan",
    "validate",
]
