"""`GET /metrics` (Phase 15): the authenticated operational-metrics endpoint
the runbook checks against the phase doc's nine-alert table
(docs/execution_phases/phase-15-deployment-observability.md §Observability).

The phase doc specifies the endpoint as *authenticated*; it deliberately does
not gate on `is_admin` (the runbook is the operator surface, and the values
here are aggregate, never per-user). Binding rate is the one `page`
alert-level metric — below 100% means the product's core claim ("every
sentence binds to a verbatim source span") is false in production, which is
a bug, not a degradation. Everything else is `degradation`.

Threshold baselines come from Phase 14's measured benchmark where one exists
(`docs/benchmark.md`, dated 2026-08-08) and are module constants with their
provenance inlined below; the two without a benchmark baseline
(`extraction_drop_rate`, `task_failure_rate`) are documented operational
defaults to be refreshed once a month of production `run_stats`/`tasks` data
accumulates. The Exa monthly allowance is documented as *allowance-as-ceiling*
(`docs/runbook.md`): Exa has no dashboard spend cap, so the $10/mo recurring
credit is the ceiling and the app-level ledger enforces it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.config import Settings
from api.web.auth import User, current_user

router = APIRouter(tags=["metrics"])

# --- Threshold baselines (all dated; see runbook for the alert table) ---
_RECENT_WINDOW = "30 days"
# Exa's recurring monthly allowance (docs/external_apis.md, Phase 01). Exa
# offers no dashboard spend cap, so this allowance IS the ceiling — the
# app-level `search_credit_usage` ledger enforces it
# (`EXA_DAILY_CREDIT_CAP_USD`), and the alert fires at 70% of the month.
SEARCH_ALLOWANCE_USD = 10.0
SEARCH_ALERT_FRACTION = 0.70
DB_CEILING_BYTES = 500 * 1024 * 1024
DB_ALERT_FRACTION = 0.70
DB_CRITICAL_FRACTION = 0.85
# Phase 14 measured mean cost/run across the ten real benchmark runs
# (docs/benchmark.md, 2026-08-08): $0.0621.
COST_PER_RUN_BASELINE_USD = 0.0621
COST_ALERT_MULTIPLIER = 2.0
# Derived RUN_TIMEOUT_S value (docs/tuning.md §5, 2026-08-08); the alert
# threshold is RUN_TIMEOUT_S x 0.8. Overridable via Settings.
DEFAULT_RUN_TIMEOUT_S = 640
RUN_TIMEOUT_ALERT_FRACTION = 0.8
# Operational defaults, no benchmark baseline yet (see module docstring).
DROP_RATE_BASELINE = 0.20
DROP_RATE_ALERT_MULTIPLIER = 1.5
TASK_FAILURE_BASELINE = 0.05
TASK_FAILURE_ALERT_MULTIPLIER = 2.0
# Fallback when Settings.global_runs_per_day is unset (the derived value from
# docs/tuning.md §5).
DEFAULT_GLOBAL_RUNS_PER_DAY = 4


class Metric(BaseModel):
    key: str
    label: str
    value: float | None
    unit: str
    threshold: float | None
    breached: bool
    alert_level: str
    note: str


class MetricsResponse(BaseModel):
    generated_at: datetime
    db_size_bytes: int
    metrics: list[Metric]


def _metric(
    *,
    key: str,
    label: str,
    value: float | None,
    unit: str,
    threshold: float | None,
    alert_level: str,
    note: str,
    below: bool = False,
) -> Metric:
    """`below=True` inverts the breach direction — binding rate breaches
    when it drops *under* 100%, every other threshold is an upper bound."""
    if value is None or threshold is None:
        breached = False
    elif below:
        breached = value < threshold
    else:
        breached = value > threshold
    return Metric(
        key=key,
        label=label,
        value=value,
        unit=unit,
        threshold=threshold,
        breached=breached,
        alert_level=alert_level,
        note=note,
    )


async def _collect_metrics(pool: asyncpg.Pool, settings: Settings) -> list[Metric]:
    metrics: list[Metric] = []

    runs_today = int(
        await pool.fetchval(
            "SELECT count(*) FROM runs WHERE started_at >= date_trunc('day', now())"
        )
        or 0
    )
    runs_threshold = float(settings.global_runs_per_day or DEFAULT_GLOBAL_RUNS_PER_DAY)
    metrics.append(
        _metric(
            key="runs_today",
            label="runs started today",
            value=float(runs_today),
            unit="runs",
            threshold=runs_threshold,
            alert_level="degradation",
            note="approaching GLOBAL_RUNS_PER_DAY; a miss trips the kill switch to reports-only",
        )
    )

    cost_row = await pool.fetchval(
        "SELECT avg(cost_usd) FROM runs WHERE cost_usd IS NOT NULL "
        f"AND started_at > now() - interval '{_RECENT_WINDOW}'"
    )
    cost_value = float(cost_row) if cost_row is not None else None
    metrics.append(
        _metric(
            key="cost_per_run_mean",
            label="mean cost per run (rolling)",
            value=cost_value,
            unit="USD",
            threshold=COST_PER_RUN_BASELINE_USD * COST_ALERT_MULTIPLIER,
            alert_level="degradation",
            note=(
                "2x Phase 14 baseline "
                f"(${COST_PER_RUN_BASELINE_USD:.4f}, docs/benchmark.md 2026-08-08)"
            ),
        )
    )

    search_spend = float(
        await pool.fetchval(
            "SELECT COALESCE(sum(credits_usd), 0) FROM search_credit_usage "
            "WHERE spent_at >= date_trunc('month', now())"
        )
        or 0
    )
    metrics.append(
        _metric(
            key="search_spend_mtd",
            label="search spend month-to-date",
            value=search_spend,
            unit="USD",
            threshold=SEARCH_ALLOWANCE_USD * SEARCH_ALERT_FRACTION,
            alert_level="degradation",
            note=(
                "Exa allowance-as-ceiling: no vendor-side spend cap exists, so the "
                f"${SEARCH_ALLOWANCE_USD:.0f}/mo allowance is the ceiling (docs/runbook.md)"
            ),
        )
    )

    binding_row = await pool.fetchrow(
        """
        SELECT count(*) FILTER (WHERE position(c.quote IN s.extracted_text) > 0) AS bound,
               count(*) FILTER (WHERE position(c.quote IN s.extracted_text) = 0) AS unbound
          FROM claims c
          JOIN sources s ON s.id = c.source_id
          JOIN runs r ON r.id = c.run_id
         WHERE s.extracted_text IS NOT NULL
           AND r.started_at > now() - interval '30 days'
        """
    )
    assert binding_row is not None
    checkable = int(binding_row["bound"]) + int(binding_row["unbound"])
    binding_value = (int(binding_row["bound"]) / checkable) if checkable else None
    metrics.append(
        _metric(
            key="sentence_binding_rate",
            label="sentence binding rate (recent, checkable sources)",
            value=binding_value,
            unit="fraction",
            threshold=1.0,
            alert_level="page",
            below=True,
            note=(
                "live position() check over recent claims vs stored extracted_text; "
                "sources whose text was evicted are excluded. Below 100% pages immediately"
            ),
        )
    )

    drop_row = await pool.fetchrow(
        """
        SELECT COALESCE(SUM(e.n), 0) AS drops, COALESCE(SUM(rs.claims_bound), 0) AS bound
          FROM run_stats rs
          CROSS JOIN LATERAL (
              SELECT COALESCE(SUM(v::bigint), 0) AS n
                FROM jsonb_each_text(rs.claims_dropped) AS e(k, v)
          ) e
         WHERE rs.recorded_at > now() - interval '30 days'
        """
    )
    assert drop_row is not None
    drop_total = int(drop_row["drops"]) + int(drop_row["bound"])
    drop_value = (int(drop_row["drops"]) / drop_total) if drop_total else None
    metrics.append(
        _metric(
            key="extraction_drop_rate",
            label="extraction drop rate (recent)",
            value=drop_value,
            unit="fraction",
            threshold=DROP_RATE_BASELINE * DROP_RATE_ALERT_MULTIPLIER,
            alert_level="degradation",
            note=(
                f"1.5x operational baseline ({DROP_RATE_BASELINE:.2f}, Phase 14 benchmark-derived "
                "estimate — refresh after ~1 month of production run_stats)"
            ),
        )
    )

    p95_row = await pool.fetchval(
        """
        SELECT percentile_cont(0.95) WITHIN GROUP (
                 ORDER BY extract(epoch FROM (finished_at - started_at))
               )
          FROM runs
         WHERE finished_at IS NOT NULL AND started_at IS NOT NULL
           AND started_at > now() - interval '30 days'
        """
    )
    p95_value = float(p95_row) if p95_row is not None else None
    run_timeout = float(settings.run_timeout_s or DEFAULT_RUN_TIMEOUT_S)
    metrics.append(
        _metric(
            key="p95_run_latency_s",
            label="p95 run latency (recent)",
            value=p95_value,
            unit="seconds",
            threshold=run_timeout * RUN_TIMEOUT_ALERT_FRACTION,
            alert_level="degradation",
            note=(
                f"RUN_TIMEOUT_S ({run_timeout:.0f}s) x 0.8; a breach means "
                "the timeout cap is the binding constraint"
            ),
        )
    )

    failure_row = await pool.fetchval(
        "SELECT count(*) FILTER (WHERE t.status = 'failed')::float / NULLIF(count(*), 0) "
        "FROM tasks t JOIN runs r ON r.id = t.run_id "
        f"WHERE r.started_at > now() - interval '{_RECENT_WINDOW}'"
    )
    failure_value = float(failure_row) if failure_row is not None else None
    metrics.append(
        _metric(
            key="task_failure_rate",
            label="task failure rate (recent)",
            value=failure_value,
            unit="fraction",
            threshold=TASK_FAILURE_BASELINE * TASK_FAILURE_ALERT_MULTIPLIER,
            alert_level="degradation",
            note=(
                f"2x operational baseline ({TASK_FAILURE_BASELINE:.2f}, no Phase 14 measurement — "
                "refresh after ~1 month of production data)"
            ),
        )
    )

    return metrics


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics(request: Request, user: User = Depends(current_user)) -> MetricsResponse:
    pool: asyncpg.Pool = request.app.state.pool
    settings: Settings = request.app.state.settings
    db_size = int(await pool.fetchval("SELECT pg_database_size(current_database())") or 0)
    metrics = await _collect_metrics(pool, settings)
    db_pct = db_size / DB_CEILING_BYTES
    metrics.append(
        _metric(
            key="db_size_pct",
            label="database size vs 500 MB ceiling",
            value=db_pct,
            unit="fraction",
            threshold=DB_ALERT_FRACTION,
            alert_level="degradation",
            note=(
                f"alerts at {DB_ALERT_FRACTION:.0%} and {DB_CRITICAL_FRACTION:.0%} of the ceiling; "
                "eviction/pinning run nightly via the keepalive workflow"
            ),
        )
    )
    return MetricsResponse(
        generated_at=datetime.now(UTC),
        db_size_bytes=db_size,
        metrics=metrics,
    )


__all__ = ["router"]
