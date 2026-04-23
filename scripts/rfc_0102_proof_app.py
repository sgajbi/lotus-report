from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn

from app.clients.render_client import RenderClient
from app.main import app as report_app
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.models import (
    ReportInputSnapshotCreateRequest,
    ReportUpstreamCallCreateRequest,
)
from app.reporting_lineage.service import get_portfolio_review_snapshot_capture_service
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render.service import (
    PortfolioReviewRenderOrchestrationService,
    get_portfolio_review_render_orchestration_service,
)
from app.routers.report_jobs import get_report_lineage_store


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


class ProofSnapshotCaptureService:
    def __init__(
        self,
        *,
        ledger: ReportJobLedger,
        lineage_store: ReportInputSnapshotStore,
        snapshot_fixture_path: Path,
    ) -> None:
        self._ledger = ledger
        self._lineage_store = lineage_store
        self._snapshot_payload = json.loads(snapshot_fixture_path.read_text(encoding="utf-8"))

    async def capture_for_job(self, job: Any) -> Any:
        self._ledger.mark_collecting_data(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        snapshot = self._lineage_store.create_snapshot(
            ReportInputSnapshotCreateRequest(
                report_job_id=job.job_id,
                report_type=job.report_type,
                report_data_contract_version="portfolio_review.v1",
                portfolio_scope=job.portfolio_scope,
                as_of_date=job.as_of_date,
                snapshot_payload=self._snapshot_payload,
                snapshot_storage_ref=None,
                supportability_status="complete",
                completeness_status="complete",
                lineage_summary={
                    "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
                    "call_count": 3,
                    "supportability_status": "complete",
                    "partial_call_count": 0,
                    "unavailable_call_count": 0,
                    "not_supported_call_count": 0,
                    "redacted_call_count": 0,
                },
                captured_at=datetime.now(UTC),
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        )
        self._lineage_store.create_upstream_calls(
            snapshot_id=snapshot.snapshot_id,
            calls=[
                ReportUpstreamCallCreateRequest(
                    service_name="lotus-core",
                    endpoint="/reporting/portfolio-summary/query",
                    method="POST",
                    contract_version="v1",
                    request_hash="sha256:rfc0102-core-request",
                    response_hash="sha256:rfc0102-core-response",
                    response_ref=None,
                    status_code=200,
                    latency_ms=148,
                    supportability_status="complete",
                    completeness_status="complete",
                    failure_category="none",
                    failure_message=None,
                    captured_at=datetime.now(UTC),
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                ),
                ReportUpstreamCallCreateRequest(
                    service_name="lotus-performance",
                    endpoint="/performance/workspace-summary",
                    method="GET",
                    contract_version="v1",
                    request_hash="sha256:rfc0102-performance-request",
                    response_hash="sha256:rfc0102-performance-response",
                    response_ref=None,
                    status_code=200,
                    latency_ms=204,
                    supportability_status="complete",
                    completeness_status="complete",
                    failure_category="none",
                    failure_message=None,
                    captured_at=datetime.now(UTC),
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                ),
                ReportUpstreamCallCreateRequest(
                    service_name="lotus-risk",
                    endpoint="/risk/review-summary",
                    method="GET",
                    contract_version="v1",
                    request_hash="sha256:rfc0102-risk-request",
                    response_hash="sha256:rfc0102-risk-response",
                    response_ref=None,
                    status_code=200,
                    latency_ms=176,
                    supportability_status="complete",
                    completeness_status="complete",
                    failure_category="none",
                    failure_message=None,
                    captured_at=datetime.now(UTC),
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                ),
            ],
        )
        return self._ledger.mark_data_ready(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )


class RecordingRenderClient:
    def __init__(
        self,
        *,
        inner: RenderClient,
        request_capture_path: Path,
        response_capture_path: Path,
    ) -> None:
        self._inner = inner
        self._request_capture_path = request_capture_path
        self._response_capture_path = response_capture_path

    async def submit_render_package(
        self,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        self._request_capture_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        status_code, response_payload = await self._inner.submit_render_package(
            payload,
            correlation_id=correlation_id,
        )
        self._response_capture_path.write_text(
            json.dumps(
                {
                    "status_code": status_code,
                    "payload": response_payload,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return status_code, response_payload


def build_app() -> Any:
    ledger = ReportJobLedger(Path(_required_env("RFC0102_LEDGER_PATH")))
    lineage_store = ReportInputSnapshotStore(Path(_required_env("RFC0102_LINEAGE_PATH")))
    capture_service = ProofSnapshotCaptureService(
        ledger=ledger,
        lineage_store=lineage_store,
        snapshot_fixture_path=Path(_required_env("RFC0102_SNAPSHOT_FIXTURE_PATH")),
    )
    render_client = RecordingRenderClient(
        inner=RenderClient(
            base_url=_required_env("RFC0102_RENDER_BASE_URL"),
            timeout_seconds=10.0,
            max_retries=1,
            retry_backoff_seconds=0.1,
        ),
        request_capture_path=Path(_required_env("RFC0102_RENDER_REQUEST_CAPTURE_PATH")),
        response_capture_path=Path(_required_env("RFC0102_RENDER_RESPONSE_CAPTURE_PATH")),
    )
    render_service = PortfolioReviewRenderOrchestrationService(
        render_client=render_client,
        snapshot_store=lineage_store,
        job_ledger=ledger,
    )

    report_app.dependency_overrides[get_report_job_ledger] = lambda: ledger
    report_app.dependency_overrides[get_report_lineage_store] = lambda: lineage_store
    report_app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        capture_service
    )
    report_app.dependency_overrides[get_portfolio_review_render_orchestration_service] = lambda: (
        render_service
    )
    report_app.state.report_job_ledger_readiness_override = lambda: True
    report_app.state.report_input_snapshot_store_readiness_override = lambda: True
    return report_app


app = build_app()


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("RFC0102_PROOF_HOST", "127.0.0.1"),
        port=int(os.environ.get("RFC0102_PROOF_PORT", "8320")),
        log_level="info",
    )
