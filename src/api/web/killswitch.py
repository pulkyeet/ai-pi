"""Kill switch (phase doc's "Kill switch"): a database flag, not a deploy.
Trips automatically when the global daily cap is hit (see
`api.web.routes.runs`, the one caller that composes this with
`api.web.quota`); can also be flipped manually — an operator action against
`system_state` directly, since the phase doc's own Endpoints table names no
admin HTTP route for it. `GET /health` reports the state; `POST /runs`
returns this honest message rather than a generic 429.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class KillSwitchState:
    enabled: bool
    reason: str | None


async def get_state(pool: asyncpg.Pool) -> KillSwitchState:
    row = await pool.fetchrow(
        "SELECT kill_switch_enabled, kill_switch_reason FROM system_state WHERE id = 1"
    )
    assert row is not None
    return KillSwitchState(enabled=row["kill_switch_enabled"], reason=row["kill_switch_reason"])


async def trip(pool: asyncpg.Pool, reason: str) -> None:
    await pool.execute(
        """
        UPDATE system_state
        SET kill_switch_enabled = true, kill_switch_reason = $1, updated_at = now()
        WHERE id = 1
        """,
        reason,
    )


async def reset(pool: asyncpg.Pool) -> None:
    await pool.execute(
        """
        UPDATE system_state
        SET kill_switch_enabled = false, kill_switch_reason = NULL, updated_at = now()
        WHERE id = 1
        """
    )


__all__ = ["KillSwitchState", "get_state", "reset", "trip"]
