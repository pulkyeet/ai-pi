from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from api.models.plan import Plan, TaskKind


class PlanCreatedEvent(BaseModel):
    type: Literal["plan.created"] = "plan.created"
    run_id: str
    plan: Plan


class TaskStartedEvent(BaseModel):
    type: Literal["task.started"] = "task.started"
    run_id: str
    task_id: int
    kind: TaskKind


class TaskCompletedEvent(BaseModel):
    type: Literal["task.completed"] = "task.completed"
    run_id: str
    task_id: int
    kind: TaskKind
    cost_usd: float | None = None
    latency_ms: int | None = None


class TaskFailedEvent(BaseModel):
    type: Literal["task.failed"] = "task.failed"
    run_id: str
    task_id: int
    kind: TaskKind
    error: str


class FindingAddedEvent(BaseModel):
    type: Literal["finding.added"] = "finding.added"
    run_id: str
    finding_id: int
    kind: str
    statement: str


class ReportReadyEvent(BaseModel):
    type: Literal["report.ready"] = "report.ready"
    run_id: str


RunEvent = Annotated[
    PlanCreatedEvent
    | TaskStartedEvent
    | TaskCompletedEvent
    | TaskFailedEvent
    | FindingAddedEvent
    | ReportReadyEvent,
    Field(discriminator="type"),
]
