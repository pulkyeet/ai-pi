"""`GET /health` (phase doc's Endpoints table): liveness plus kill-switch
state, no auth."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.web import killswitch

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    kill_switch_enabled: bool
    kill_switch_reason: str | None


@router.get("/health", response_model=HealthResponse)
async def get_health(request: Request) -> HealthResponse:
    state = await killswitch.get_state(request.app.state.pool)
    return HealthResponse(
        status="ok", kill_switch_enabled=state.enabled, kill_switch_reason=state.reason
    )


__all__ = ["router"]
