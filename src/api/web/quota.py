"""Quotas, concurrency, and guardrails (phase doc's "Quotas and guardrails").

Two different kinds of cap, deliberately different mechanisms:

  - **Per-user / global daily quota** — a historical count over `runs`. A
    conditional `INSERT ... SELECT ... WHERE count(*) < quota` (masterplan
    §8.3's own SQL sketch) reads right, but on its own is **not** actually
    atomic under concurrency: Postgres's default `READ COMMITTED` isolation
    lets two simultaneous statements each see the pre-insert count and both
    admit — the exact race a quota exists to prevent, reproduced by this
    module's own atomicity test before this fix (`hashtext`-keyed
    `pg_advisory_xact_lock` added below). The lock serializes concurrent
    checks for the same resource down to one at a time; the conditional
    `INSERT` inside that lock is what actually decides admit-or-reject.
  - **Concurrency** — how many runs are *live right now*, a property of this
    process, not the database. Kept in-memory, per-process — the same
    accepted single-worker limitation `api.executor.budget` (Phase 02)
    already established for run-budget tracking: correct for the default
    single-worker deployment; a second worker process against the same run
    pool would need a cross-process tracker, promoted only if that
    deployment shape is ever real (see docs/working_knowledge.md).

Both stay unenforced (`None`) until Phase 14 measures real numbers — every
knob here follows `api.config.Settings`' "`None` means unconfigured, never
a crash" convention.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import asyncpg

from api.web.errors import QuotaExceededError


async def try_create_run(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    user_id: UUID,
    query: str,
    per_user_quota: int | None,
    global_quota: int | None,
) -> None:
    """Atomically inserts a `pending` run iff both the per-user and global
    24h windows have headroom. Raises `QuotaExceededError` rather than
    returning a bool — callers cannot forget to check it.

    Two advisory locks (transaction-scoped, auto-released on commit/
    rollback) serialize the check: a fixed key for the global cap, then a
    `hashtext(user_id)` key for the per-user cap — always acquired in that
    order, so no caller can ever deadlock against another. Skipped entirely
    when the corresponding cap is `None` (unenforced), so an unconfigured
    quota costs nothing.
    """
    async with pool.acquire() as conn, conn.transaction():
        if global_quota is not None:
            await conn.execute("SELECT pg_advisory_xact_lock(0)")
        if per_user_quota is not None:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1)::bigint)", str(user_id))

        row = await conn.fetchrow(
            """
            INSERT INTO runs (id, user_id, query, status, started_at)
            SELECT $1, $2, $3, 'pending', now()
            WHERE ($4::int IS NULL OR (
                      SELECT count(*) FROM runs
                      WHERE user_id = $2 AND started_at > now() - interval '1 day'
                  ) < $4)
              AND ($5::int IS NULL OR (
                      SELECT count(*) FROM runs
                      WHERE started_at > now() - interval '1 day'
                  ) < $5)
            RETURNING id
            """,
            run_id,
            user_id,
            query,
            per_user_quota,
            global_quota,
        )
        if row is not None:
            return

    # The atomic INSERT above already decided pass/fail; this second,
    # non-atomic read only picks which error message is honest — it cannot
    # itself let an over-limit run through.
    if per_user_quota is not None:
        user_count = await pool.fetchval(
            "SELECT count(*) FROM runs "
            "WHERE user_id = $1 AND started_at > now() - interval '1 day'",
            user_id,
        )
        if user_count is not None and user_count >= per_user_quota:
            raise QuotaExceededError(
                "daily run quota reached for this account", code="user_quota_exceeded"
            )
    raise QuotaExceededError(
        "the global daily run quota has been reached", code="global_quota_exceeded"
    )


class ConcurrencyQueue:
    """FIFO admission control over `max_concurrent` live runs, with a
    visible position for anyone still waiting (phase doc: "a visible
    position is a much better experience than an opaque rejection").
    `max_concurrent=None` means unenforced — every run is admitted
    immediately, position is always 0.
    """

    def __init__(self, max_concurrent: int | None) -> None:
        self._max_concurrent = max_concurrent
        self._order: list[str] = []
        self._running: set[str] = set()
        self._admitted: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, run_id: str) -> None:
        """Registers `run_id` and blocks until it is admitted — immediately
        if there is headroom, otherwise once an earlier run releases."""
        event = asyncio.Event()
        async with self._lock:
            self._order.append(run_id)
            self._admitted[run_id] = event
            if self._max_concurrent is None or len(self._running) < self._max_concurrent:
                self._running.add(run_id)
                event.set()
        await event.wait()

    async def release(self, run_id: str) -> None:
        async with self._lock:
            self._running.discard(run_id)
            self._admitted.pop(run_id, None)
            if run_id in self._order:
                self._order.remove(run_id)
            if self._max_concurrent is not None:
                for candidate in self._order:
                    if candidate not in self._running:
                        self._running.add(candidate)
                        self._admitted[candidate].set()
                        break

    def position(self, run_id: str) -> int:
        """0 once running (or when concurrency is unenforced); otherwise a
        1-based position among runs still waiting, in FIFO order."""
        if self._max_concurrent is None or run_id in self._running:
            return 0
        waiting = [r for r in self._order if r not in self._running]
        try:
            return waiting.index(run_id) + 1
        except ValueError:
            return 0


__all__ = ["ConcurrencyQueue", "try_create_run"]
