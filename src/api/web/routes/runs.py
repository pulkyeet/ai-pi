"""`/runs` routes (phase doc's Endpoints table): run creation with the
Turnstile -> validation -> quota -> concurrency gate order, status/report/
drill-down reads, disambiguation resolution, and the SSE stream.

**Concurrency admission is deliberately not awaited here.** It happens
inside `api.web.runner.run_pipeline`'s first line, in the background task —
still strictly before any spend (`interpret()`'s first LLM call is the line
after), but without holding the HTTP request open. `GET /runs/{id}` reports
`queue_position` for a caller to poll — the phase doc's own Open Decision
#2 leans this way explicitly ("Lean polling until the run starts").
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

import asyncpg
import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from api.config import Settings
from api.models.brief import ResearchBrief
from api.planner.interpret import QueryRejectedError, validate_query
from api.web import killswitch, sse, turnstile
from api.web.auth import User, current_user, optional_user
from api.web.errors import (
    ConflictError,
    KillSwitchActiveError,
    NotFoundError,
    QueryRejectedAPIError,
    QuotaExceededError,
    TurnstileFailedError,
)
from api.web.quota import ConcurrencyQueue, try_create_run
from api.web.runner import run_pipeline

router = APIRouter(prefix="/runs", tags=["runs"])


def _spawn(request: Request, coro: Any) -> None:
    """Fire-and-forget a background task, keeping a strong reference on
    `app.state` so it is never garbage-collected mid-run (a bare
    `asyncio.create_task` result with no reference is only weakly held)."""
    task = asyncio.create_task(coro)
    tasks: set[asyncio.Task[None]] = request.app.state.background_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


def _authorize_row(row: asyncpg.Record | None, user: User | None) -> asyncpg.Record:
    if row is None:
        raise NotFoundError("no such run")
    if row["is_public"]:
        return row
    if user is not None and str(row["user_id"]) == str(user.id):
        return row
    # 404, not 403: a non-public run's existence is not disclosed to a
    # caller who cannot read it either way.
    raise NotFoundError("no such run")


def _load_jsonb(value: str | dict[str, Any] | list[Any] | None) -> Any:
    if value is None:
        return None
    return json.loads(value) if isinstance(value, str) else value


class CreateRunRequest(BaseModel):
    query: str
    turnstile_token: str | None = None


class RunAccepted(BaseModel):
    run_id: str
    status: str
    disambiguation_fields: list[str] = []


@router.post("", response_model=RunAccepted, status_code=202)
async def create_run(
    body: CreateRunRequest, request: Request, user: User = Depends(current_user)
) -> RunAccepted:
    settings: Settings = request.app.state.settings
    pool: asyncpg.Pool = request.app.state.pool
    http: httpx.AsyncClient = request.app.state.http

    kill_state = await killswitch.get_state(pool)
    if kill_state.enabled:
        raise KillSwitchActiveError(
            kill_state.reason
            or "today's free usage cap has been reached; live runs resume tomorrow"
        )

    turnstile_secret = (
        settings.turnstile_secret_key.get_secret_value() if settings.turnstile_secret_key else None
    )
    remote_ip = request.client.host if request.client else None
    if not await turnstile.verify(
        http, secret_key=turnstile_secret, token=body.turnstile_token, remote_ip=remote_ip
    ):
        raise TurnstileFailedError("bot check failed")

    try:
        validate_query(body.query)
    except QueryRejectedError as exc:
        raise QueryRejectedAPIError(exc.detail, code=exc.reason.value) from exc

    run_id = f"r_{uuid.uuid4().hex}"
    per_user_quota = (
        user.quota_override if user.quota_override is not None else settings.runs_per_user_per_day
    )
    try:
        await try_create_run(
            pool,
            run_id=run_id,
            user_id=user.id,
            query=body.query,
            per_user_quota=per_user_quota,
            global_quota=settings.global_runs_per_day,
        )
    except QuotaExceededError as exc:
        if exc.code == "global_quota_exceeded":
            await killswitch.trip(pool, "global daily run quota reached")
            raise KillSwitchActiveError(
                "today's free usage cap has been reached; live runs resume tomorrow"
            ) from exc
        raise

    concurrency_queue: ConcurrencyQueue = request.app.state.concurrency_queue
    _spawn(
        request,
        run_pipeline(
            pool,
            http,
            settings,
            run_id=run_id,
            query=body.query,
            concurrency_queue=concurrency_queue,
        ),
    )
    return RunAccepted(run_id=run_id, status="pending")


class RunStatus(BaseModel):
    run_id: str
    query: str
    status: str
    cost_usd: float | None
    coverage: float | None
    brief: dict[str, Any] | None
    disambiguation_fields: list[str] | None
    queue_position: int


@router.get("/{run_id}", response_model=RunStatus)
async def get_run(
    run_id: str, request: Request, user: User | None = Depends(optional_user)
) -> RunStatus:
    pool: asyncpg.Pool = request.app.state.pool
    row = await pool.fetchrow(
        "SELECT id, user_id, query, status, cost_usd, coverage, brief, disambiguation_fields, "
        "is_public FROM runs WHERE id = $1",
        run_id,
    )
    row = _authorize_row(row, user)
    queue: ConcurrencyQueue = request.app.state.concurrency_queue
    return RunStatus(
        run_id=row["id"],
        query=row["query"],
        status=row["status"],
        cost_usd=float(row["cost_usd"]) if row["cost_usd"] is not None else None,
        coverage=float(row["coverage"]) if row["coverage"] is not None else None,
        brief=_load_jsonb(row["brief"]),
        disambiguation_fields=_load_jsonb(row["disambiguation_fields"]),
        queue_position=queue.position(run_id),
    )


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: str, request: Request, user: User | None = Depends(optional_user)
) -> EventSourceResponse:
    pool: asyncpg.Pool = request.app.state.pool
    row = await pool.fetchrow("SELECT user_id, is_public FROM runs WHERE id = $1", run_id)
    _authorize_row(row, user)

    last_event_id = request.headers.get("last-event-id")
    since_id = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    return EventSourceResponse(
        sse.stream_events(pool, run_id, since_id=since_id),
        ping=15,
        headers={"X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}/report.json")
async def get_report(
    run_id: str, request: Request, user: User | None = Depends(optional_user)
) -> dict[str, Any]:
    pool: asyncpg.Pool = request.app.state.pool
    run_row = await pool.fetchrow("SELECT user_id, is_public FROM runs WHERE id = $1", run_id)
    _authorize_row(run_row, user)

    report_row = await pool.fetchrow("SELECT payload FROM reports WHERE run_id = $1", run_id)
    if report_row is None:
        raise NotFoundError("report not ready yet")
    payload = report_row["payload"]
    return dict(json.loads(payload) if isinstance(payload, str) else payload)


class OtherClaim(BaseModel):
    claim_id: int
    attribute: str
    value_text: str | None
    value_num: float | None
    quote: str


class ClaimDrilldown(BaseModel):
    claim_id: int
    attribute: str
    value_text: str | None
    value_num: float | None
    quote: str
    char_start: int
    char_end: int
    quote_context: str
    context_offset: int
    source_url: str
    source_text: str | None
    source_fetched_at: datetime
    grade: str
    confidence: float
    confidence_inputs: dict[str, Any] | None
    other_claims: list[OtherClaim]


@router.get("/{run_id}/claims/{claim_id}", response_model=ClaimDrilldown)
async def get_claim_drilldown(
    run_id: str, claim_id: int, request: Request, user: User | None = Depends(optional_user)
) -> ClaimDrilldown:
    pool: asyncpg.Pool = request.app.state.pool
    run_row = await pool.fetchrow("SELECT user_id, is_public FROM runs WHERE id = $1", run_id)
    _authorize_row(run_row, user)

    row = await pool.fetchrow(
        """
        SELECT c.id, c.attribute, c.value_text, c.value_num, c.quote, c.char_start, c.char_end,
               c.quote_context, c.context_offset, c.grade, c.confidence, c.confidence_inputs,
               c.source_id, s.canonical_url, s.extracted_text, s.fetched_at
        FROM claims c JOIN sources s ON s.id = c.source_id
        WHERE c.run_id = $1 AND c.id = $2
        """,
        run_id,
        claim_id,
    )
    if row is None:
        raise NotFoundError("no such claim")

    # "Other claims from this source" (phase doc's drill-down panel design):
    # every other claim bound to the same source, in this same run.
    other_rows = await pool.fetch(
        """
        SELECT id, attribute, value_text, value_num, quote
        FROM claims
        WHERE run_id = $1 AND source_id = $2 AND id != $3
        ORDER BY id
        """,
        run_id,
        row["source_id"],
        claim_id,
    )
    return ClaimDrilldown(
        claim_id=row["id"],
        attribute=row["attribute"],
        value_text=row["value_text"],
        value_num=float(row["value_num"]) if row["value_num"] is not None else None,
        quote=row["quote"],
        char_start=row["char_start"],
        char_end=row["char_end"],
        quote_context=row["quote_context"],
        context_offset=row["context_offset"],
        source_url=row["canonical_url"],
        source_text=row["extracted_text"],
        source_fetched_at=row["fetched_at"],
        grade=row["grade"],
        confidence=float(row["confidence"]),
        confidence_inputs=_load_jsonb(row["confidence_inputs"]),
        other_claims=[
            OtherClaim(
                claim_id=r["id"],
                attribute=r["attribute"],
                value_text=r["value_text"],
                value_num=float(r["value_num"]) if r["value_num"] is not None else None,
                quote=r["quote"],
            )
            for r in other_rows
        ],
    )


class FindingDrilldown(BaseModel):
    finding_id: int
    statement: str
    claim_ids: list[int]


@router.get("/{run_id}/findings/{finding_id}", response_model=FindingDrilldown)
async def get_finding_drilldown(
    run_id: str, finding_id: int, request: Request, user: User | None = Depends(optional_user)
) -> FindingDrilldown:
    """`MVP.addresses_finding_ids`/`Risk.addresses_finding_ids`/
    `FeatureGap.addresses_finding_ids` (Report, Phase 11) name a
    `findings.id`, not a `claims.id` — the one hop `ClaimDrilldown` can't
    take. `findings_must_cite` (migration `0001`) guarantees `claim_ids` is
    never empty, so every synthesised statement resolves to a real claim the
    same way a competitor/pricing/pain-point entry already does (phase doc:
    "findings is the only table whose text ever reaches a user, and it
    always carries claim_ids... the entire drill down mechanism").
    """
    pool: asyncpg.Pool = request.app.state.pool
    run_row = await pool.fetchrow("SELECT user_id, is_public FROM runs WHERE id = $1", run_id)
    _authorize_row(run_row, user)

    row = await pool.fetchrow(
        "SELECT id, statement, claim_ids FROM findings WHERE run_id = $1 AND id = $2",
        run_id,
        finding_id,
    )
    if row is None:
        raise NotFoundError("no such finding")
    return FindingDrilldown(
        finding_id=row["id"], statement=row["statement"], claim_ids=list(row["claim_ids"])
    )


class ResolveDisambiguationRequest(BaseModel):
    category: str | None = None
    segment: str | None = None
    geography: str | None = None
    monetisation_guess: str | None = None


@router.patch("/{run_id}", response_model=RunAccepted, status_code=202)
async def resolve_disambiguation(
    run_id: str,
    body: ResolveDisambiguationRequest,
    request: Request,
    user: User = Depends(current_user),
) -> RunAccepted:
    pool: asyncpg.Pool = request.app.state.pool
    row = await pool.fetchrow(
        "SELECT user_id, status, query, brief, keywords FROM runs WHERE id = $1", run_id
    )
    if row is None or str(row["user_id"]) != str(user.id):
        raise NotFoundError("no such run")
    if row["status"] != "needs_input":
        raise ConflictError("run is not waiting for input")

    brief_data = _load_jsonb(row["brief"]) or {}
    overrides = body.model_dump(exclude_none=True)
    resolved_brief = ResearchBrief.model_validate({**brief_data, **overrides})
    keywords = _load_jsonb(row["keywords"]) or []

    settings: Settings = request.app.state.settings
    http: httpx.AsyncClient = request.app.state.http
    concurrency_queue: ConcurrencyQueue = request.app.state.concurrency_queue
    _spawn(
        request,
        run_pipeline(
            pool,
            http,
            settings,
            run_id=run_id,
            query=row["query"],
            concurrency_queue=concurrency_queue,
            resume_brief=resolved_brief,
            resume_keywords=keywords,
        ),
    )
    return RunAccepted(run_id=run_id, status="running")


__all__ = ["router"]
