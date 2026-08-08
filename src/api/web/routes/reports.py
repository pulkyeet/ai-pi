"""`GET /reports/benchmark` (phase doc's Endpoints table): the public
homepage set, no auth — masterplan §8.3's layered defence starts here:
"most visitors never trigger a live run."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.models.report import Report

router = APIRouter(prefix="/reports", tags=["reports"])


class BenchmarkReportSummary(BaseModel):
    run_id: str
    query: str
    report: Report


@router.get("/benchmark", response_model=list[BenchmarkReportSummary])
async def list_benchmark_reports(request: Request) -> list[BenchmarkReportSummary]:
    rows = await request.app.state.pool.fetch(
        """
        SELECT r.id AS run_id, r.query, rep.payload
        FROM runs r JOIN reports rep ON rep.run_id = r.id
        WHERE r.is_benchmark = true AND r.is_public = true
        ORDER BY r.finished_at DESC NULLS LAST
        """
    )
    return [
        BenchmarkReportSummary(
            run_id=row["run_id"],
            query=row["query"],
            report=Report.model_validate(
                json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
            ),
        )
        for row in rows
    ]


__all__ = ["router"]
