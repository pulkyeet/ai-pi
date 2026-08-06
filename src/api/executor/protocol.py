"""Domain-agnostic contracts for the executor.

Deliberately independent of `api.models.plan` / `api.models.events`: those
carry the closed, real-domain `TaskKind` vocabulary (discover_competitors,
profile_product, ...), while this module's `kind` is a plain `str` so the
executor can be hardened against synthetic tasks (sleep_task, spawn_task, ...)
without touching the frozen Phase 00 contracts. Phase 10 adapts real `Plan`s
into an `ExecutionPlan` at the boundary; nothing in `api.executor` imports
`api.models.plan` or `api.models.events`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

ServiceName = Literal["search", "crawl", "llm", "none"]


class TaskSpec(BaseModel):
    node_key: str
    kind: str
    args: dict[str, Any] = Field(default_factory=dict)
    budget_weight: int = 1
    depends_on: list[str] = Field(default_factory=list)
    priority: int = 0


class SpawnRequest(BaseModel):
    node_key: str
    kind: str
    args: dict[str, Any] = Field(default_factory=dict)
    budget_weight: int = 1
    depends_on: list[str] = Field(default_factory=list)
    priority: int = 0


class ExecutionPlan(BaseModel):
    tasks: list[TaskSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _graph_is_valid(self) -> ExecutionPlan:
        keys = [t.node_key for t in self.tasks]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate node_key in plan")

        key_set = set(keys)
        edges: list[tuple[str, str]] = []
        for t in self.tasks:
            for dep in t.depends_on:
                if dep not in key_set:
                    raise ValueError(f"task {t.node_key!r} depends on undeclared node {dep!r}")
                edges.append((dep, t.node_key))

        if _has_cycle(keys, edges):
            raise ValueError("plan graph must be acyclic")
        return self


def _has_cycle(node_keys: list[str], edges: list[tuple[str, str]]) -> bool:
    adjacency: dict[str, list[str]] = {k: [] for k in node_keys}
    for a, b in edges:
        adjacency[a].append(b)

    white, gray, black = 0, 1, 2
    color = dict.fromkeys(node_keys, white)

    def visit(node: str) -> bool:
        color[node] = gray
        for neighbor in adjacency[node]:
            if color[neighbor] == gray:
                return True
            if color[neighbor] == white and visit(neighbor):
                return True
        color[node] = black
        return False

    return any(color[n] == white and visit(n) for n in node_keys)


class HandlerResult(BaseModel):
    cost_usd: float = 0.0
    artifacts: dict[str, Any] = Field(default_factory=dict)
    spawned: list[SpawnRequest] = Field(default_factory=list)


@dataclass
class TaskContext:
    run_id: str
    task_id: int
    node_key: str
    kind: str
    lease_token: UUID
    attempt: int
    emit: Callable[[ExecutorEvent], Awaitable[None]]
    renew_lease: Callable[[], Awaitable[bool]]


class TaskHandler(Protocol):
    kind: str
    cost_weight: int
    service: ServiceName
    timeout_s: float

    async def run(self, ctx: TaskContext, args: dict[str, Any]) -> HandlerResult: ...


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, TaskHandler] = {}

    def register(self, handler: TaskHandler) -> None:
        self._handlers[handler.kind] = handler

    def get(self, kind: str) -> TaskHandler:
        try:
            return self._handlers[kind]
        except KeyError:
            raise KeyError(f"no handler registered for task kind {kind!r}") from None


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class TaskStarted(BaseModel):
    type: Literal["task.started"] = "task.started"
    run_id: str
    task_id: int
    node_key: str
    kind: str


class TaskProgress(BaseModel):
    type: Literal["task.progress"] = "task.progress"
    run_id: str
    task_id: int
    node_key: str
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskCompleted(BaseModel):
    type: Literal["task.completed"] = "task.completed"
    run_id: str
    task_id: int
    node_key: str
    kind: str
    cost_usd: float
    latency_ms: int


class TaskFailed(BaseModel):
    type: Literal["task.failed"] = "task.failed"
    run_id: str
    task_id: int
    node_key: str
    kind: str
    error: str
    attempts: int


class TaskSkipped(BaseModel):
    type: Literal["task.skipped"] = "task.skipped"
    run_id: str
    task_id: int
    node_key: str
    kind: str
    reason: str


class RunFinished(BaseModel):
    type: Literal["run.finished"] = "run.finished"
    run_id: str
    done: int
    failed: int
    skipped: int
    coverage: float


ExecutorEvent = Annotated[
    TaskStarted | TaskProgress | TaskCompleted | TaskFailed | TaskSkipped | RunFinished,
    Field(discriminator="type"),
]

EVENT_TYPES: dict[str, type[BaseModel]] = {
    "task.started": TaskStarted,
    "task.progress": TaskProgress,
    "task.completed": TaskCompleted,
    "task.failed": TaskFailed,
    "task.skipped": TaskSkipped,
    "run.finished": RunFinished,
}
