from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class TaskKind(StrEnum):
    DISCOVER_COMPETITORS = "discover_competitors"
    PROFILE_PRODUCT = "profile_product"
    EXTRACT_PRICING = "extract_pricing"
    MINE_COMMUNITY = "mine_community"
    OSS_PROFILE = "oss_profile"
    FIND_FUNDING = "find_funding"
    TREND_SIGNALS = "trend_signals"


TASK_COST_WEIGHT: dict[TaskKind, int] = {
    TaskKind.DISCOVER_COMPETITORS: 3,
    TaskKind.PROFILE_PRODUCT: 2,
    TaskKind.EXTRACT_PRICING: 1,
    TaskKind.MINE_COMMUNITY: 4,
    TaskKind.OSS_PROFILE: 1,
    TaskKind.FIND_FUNDING: 1,
    TaskKind.TREND_SIGNALS: 2,
}

TASK_ARGS: dict[TaskKind, tuple[str, ...]] = {
    TaskKind.DISCOVER_COMPETITORS: ("query_variants",),
    TaskKind.PROFILE_PRODUCT: ("entity_key",),
    TaskKind.EXTRACT_PRICING: ("entity_key",),
    TaskKind.MINE_COMMUNITY: ("keywords", "venues"),
    TaskKind.OSS_PROFILE: ("repo",),
    TaskKind.FIND_FUNDING: ("entity_key",),
    TaskKind.TREND_SIGNALS: ("keywords",),
}


class PlanNode(BaseModel):
    id: str
    kind: TaskKind
    args: dict[str, Any] = Field(default_factory=dict)
    budget_weight: int

    @model_validator(mode="after")
    def _args_declared(self) -> PlanNode:
        missing = [a for a in TASK_ARGS[self.kind] if a not in self.args]
        if missing:
            raise ValueError(f"{self.kind} node {self.id!r} missing required args: {missing}")
        return self


class Plan(BaseModel):
    nodes: list[PlanNode]
    edges: list[tuple[str, str]] = Field(default_factory=list)
    total_budget_weight: int

    @model_validator(mode="after")
    def _graph_is_valid(self) -> Plan:
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate node ids in plan")

        id_set = set(ids)
        for a, b in self.edges:
            if a not in id_set or b not in id_set:
                raise ValueError(f"edge references an undeclared node: ({a!r}, {b!r})")

        if _has_cycle(ids, self.edges):
            raise ValueError("plan graph must be acyclic")

        computed = sum(n.budget_weight for n in self.nodes)
        if computed != self.total_budget_weight:
            raise ValueError(
                f"total_budget_weight ({self.total_budget_weight}) "
                f"!= sum of node weights ({computed})"
            )
        return self


def _has_cycle(node_ids: list[str], edges: list[tuple[str, str]]) -> bool:
    adjacency: dict[str, list[str]] = {i: [] for i in node_ids}
    for a, b in edges:
        adjacency[a].append(b)

    white, gray, black = 0, 1, 2
    color = dict.fromkeys(node_ids, white)

    def visit(node: str) -> bool:
        color[node] = gray
        for neighbor in adjacency[node]:
            if color[neighbor] == gray:
                return True
            if color[neighbor] == white and visit(neighbor):
                return True
        color[node] = black
        return False

    return any(color[n] == white and visit(n) for n in node_ids)
