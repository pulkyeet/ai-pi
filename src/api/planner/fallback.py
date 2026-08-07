"""The deterministic fallback plan (phase doc: "A run never fails because
planning failed"). Hand-written, zero LLM calls, always passes
`api.planner.validate.validate_plan_domain` by construction — the safety
net has its own unit test (`test_fallback_plan_is_itself_valid`) precisely
because a fallback that isn't valid is not a fallback.

Covers the common case named in the phase doc: discover, then (if the
brief yielded any keywords) mine the community backbone alongside it.
`profile_product`/`extract_pricing` are not — cannot be — seeded here
either (see `api.planner.registry`'s module docstring): the executor's
`discover_competitors` handler (Phase 10) spawns them once real entities
exist. Venue defaults follow README.md's D5 backbone (HN Algolia + GitHub +
Stack Exchange), since Reddit is credential-gated and not guaranteed
available.
"""

from __future__ import annotations

from api.models.brief import ResearchBrief
from api.models.plan import Plan, PlanNode
from api.planner.registry import DEFAULT_MAX_COMPETITORS_PROFILED, TASK_COST_WEIGHT, TaskKind

DEFAULT_MINE_VENUES: tuple[str, ...] = ("hn", "github", "stackexchange")


def _query_variants(brief: ResearchBrief, keywords: list[str]) -> list[str]:
    candidates = [
        brief.category,
        f"{brief.category} {brief.segment}".strip(),
        f"{brief.category} {brief.geography}".strip(),
        *keywords,
    ]
    seen: set[str] = set()
    variants: list[str] = []
    for c in candidates:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            variants.append(c)
    return variants


def fallback_plan(
    brief: ResearchBrief,
    keywords: list[str],
    *,
    max_competitors_profiled: int = DEFAULT_MAX_COMPETITORS_PROFILED,
) -> Plan:
    discover_weight = TASK_COST_WEIGHT[TaskKind.DISCOVER_COMPETITORS] + max_competitors_profiled * (
        TASK_COST_WEIGHT[TaskKind.PROFILE_PRODUCT] + TASK_COST_WEIGHT[TaskKind.EXTRACT_PRICING]
    )
    nodes = [
        PlanNode(
            id="t1",
            kind=TaskKind.DISCOVER_COMPETITORS,
            args={
                "query_variants": _query_variants(brief, keywords),
                "max_profile_count": max_competitors_profiled,
                "consider_oss": False,
                "consider_funding": False,
            },
            budget_weight=discover_weight,
        )
    ]
    if keywords:
        nodes.append(
            PlanNode(
                id="t2",
                kind=TaskKind.MINE_COMMUNITY,
                args={"keywords": keywords, "venues": list(DEFAULT_MINE_VENUES)},
                budget_weight=TASK_COST_WEIGHT[TaskKind.MINE_COMMUNITY],
            )
        )

    return Plan(nodes=nodes, edges=[], total_budget_weight=sum(n.budget_weight for n in nodes))


__all__ = ["DEFAULT_MINE_VENUES", "fallback_plan"]
