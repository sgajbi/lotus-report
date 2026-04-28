from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.report_batch_orchestrator.service import get_report_batch_ledger
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_operations.attention import AttentionScanConfig, ReportingAttentionScanner
from app.reporting_operations.models import ReportingAttentionScanResponse

router = APIRouter(prefix="/reports/operations", tags=["Report Operations"])


@router.get(
    "/attention",
    response_model=ReportingAttentionScanResponse,
    summary="Scan reporting operations attention events",
    description=(
        "Returns a bounded source-backed attention view for active report jobs and batch items. "
        "The response includes only opaque identifiers, bounded reasons, lifecycle state, age, "
        "thresholds, and source-backed evidence links; raw report payloads, portfolio scope, "
        "tenant identifiers, trace identifiers, and correlation identifiers are not returned."
    ),
)
async def scan_reporting_attention(
    report_job_stuck_threshold_seconds: Annotated[
        int,
        Query(
            ge=1,
            le=86_400,
            description="Age threshold for active report-job stuck-state attention events.",
        ),
    ] = 900,
    batch_item_stuck_threshold_seconds: Annotated[
        int,
        Query(
            ge=1,
            le=86_400,
            description="Age threshold for active batch-item stuck-state attention events.",
        ),
    ] = 900,
    sla_breach_threshold_seconds: Annotated[
        int,
        Query(
            ge=1,
            le=604_800,
            description="Age threshold for critical SLA-breach attention events.",
        ),
    ] = 3600,
    max_events: Annotated[
        int,
        Query(
            ge=1,
            le=1000,
            description="Maximum number of attention events returned by this bounded scan.",
        ),
    ] = 250,
    report_job_ledger: Annotated[Any, Depends(get_report_job_ledger)] = None,
    batch_ledger: Annotated[Any, Depends(get_report_batch_ledger)] = None,
) -> ReportingAttentionScanResponse:
    scanner = ReportingAttentionScanner(
        report_job_ledger=report_job_ledger,
        batch_ledger=batch_ledger,
        config=AttentionScanConfig(
            report_job_stuck_threshold_seconds=report_job_stuck_threshold_seconds,
            batch_item_stuck_threshold_seconds=batch_item_stuck_threshold_seconds,
            sla_breach_threshold_seconds=sla_breach_threshold_seconds,
            max_events=max_events,
        ),
    )
    return scanner.scan()
