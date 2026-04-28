from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn

from app.clients.render_client import RenderClient
from app.main import app as report_app
from app.report_batch_orchestrator.ledger import ReportBatchLedger
from app.report_batch_orchestrator.replay import (
    ReportBatchItemReplayService,
    get_report_batch_item_replay_service,
)
from app.report_batch_orchestrator.scheduler import ReportBatchScheduler
from app.report_batch_orchestrator.service import (
    get_report_batch_ledger,
    get_report_batch_scheduler,
)
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.models import (
    ReportInputSnapshotCreateRequest,
    ReportUpstreamCallCreateRequest,
)
from app.reporting_lineage.service import get_portfolio_review_snapshot_capture_service
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render.regenerate_service import (
    PortfolioReviewRegenerateService,
    get_portfolio_review_regenerate_service,
)
from app.reporting_render.replay_service import (
    PortfolioReviewReplayService,
    get_portfolio_review_replay_service,
)
from app.reporting_render.rerender_service import (
    PortfolioReviewRerenderService,
    get_portfolio_review_rerender_service,
)
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


def _write_capture(path: Path, payload: dict[str, Any], *, sequence: int) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    path.write_text(encoded, encoding="utf-8")
    sequenced_path = path.with_name(f"{path.stem}-{sequence:02d}{path.suffix}")
    sequenced_path.write_text(encoded, encoding="utf-8")


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


class ProofPortfolioSource:
    async def get_portfolio_detail(
        self,
        portfolio_id: str,
        correlation_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        return 200, {
            "portfolio_id": portfolio_id,
            "status": "active",
            "correlation_id": correlation_id,
        }

    async def list_portfolios(
        self,
        correlation_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        return 200, {
            "portfolios": [
                {
                    "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                    "status": "active",
                }
            ],
            "correlation_id": correlation_id,
        }


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
        self._submit_count = 0

    async def submit_render_package(
        self,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        self._submit_count += 1
        _write_capture(self._request_capture_path, payload, sequence=self._submit_count)
        status_code, response_payload = await self._inner.submit_render_package(
            payload,
            correlation_id=correlation_id,
            trace_id=trace_id,
        )
        _write_capture(
            self._response_capture_path,
            {
                "status_code": status_code,
                "payload": response_payload,
            },
            sequence=self._submit_count,
        )
        return status_code, response_payload


class RecordingArchiveClient:
    def __init__(
        self,
        *,
        base_url: str,
        request_capture_path: Path,
        response_capture_path: Path,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._request_capture_path = request_capture_path
        self._response_capture_path = response_capture_path
        self._archive_count = 0

    async def archive_document(
        self,
        payload: dict[str, Any],
        *,
        actor_id: str,
        tenant_id: str,
        region: str,
        correlation_id: str,
        trace_id: str,
        booking_center_code: str | None = None,
        role: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        from app.clients.archive_client import ArchiveClient

        self._archive_count += 1
        _write_capture(self._request_capture_path, payload, sequence=self._archive_count)
        client = ArchiveClient(
            base_url=self._base_url,
            timeout_seconds=10.0,
            max_retries=1,
            retry_backoff_seconds=0.1,
        )
        status_code, response_payload = await client.archive_document(
            payload,
            actor_id=actor_id,
            tenant_id=tenant_id,
            region=region,
            correlation_id=correlation_id,
            trace_id=trace_id,
            booking_center_code=booking_center_code,
            role=role,
        )
        _write_capture(
            self._response_capture_path,
            {
                "status_code": status_code,
                "payload": response_payload,
            },
            sequence=self._archive_count,
        )
        return status_code, response_payload


def build_app() -> Any:
    ledger = ReportJobLedger(Path(_required_env("RFC0102_LEDGER_PATH")))
    lineage_store = ReportInputSnapshotStore(Path(_required_env("RFC0102_LINEAGE_PATH")))
    batch_ledger = ReportBatchLedger(Path(_required_env("RFC0102_BATCH_LEDGER_PATH")))
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
    archive_client = RecordingArchiveClient(
        base_url=_required_env("RFC0102_ARCHIVE_BASE_URL"),
        request_capture_path=Path(_required_env("RFC0102_ARCHIVE_REQUEST_CAPTURE_PATH")),
        response_capture_path=Path(_required_env("RFC0102_ARCHIVE_RESPONSE_CAPTURE_PATH")),
    )
    render_service = PortfolioReviewRenderOrchestrationService(
        render_client=render_client,
        archive_client=archive_client,
        snapshot_store=lineage_store,
        job_ledger=ledger,
    )
    rerender_service = PortfolioReviewRerenderService(
        render_client=render_client,
        archive_client=archive_client,
        snapshot_store=lineage_store,
        ledger=ledger,
    )
    regenerate_service = PortfolioReviewRegenerateService(
        ledger=ledger,
        snapshot_store=lineage_store,
        capture_service=capture_service,
        render_service=render_service,
    )
    replay_service = PortfolioReviewReplayService(
        ledger=ledger,
        capture_service=capture_service,
        render_service=render_service,
    )
    batch_replay_service = ReportBatchItemReplayService(
        batch_ledger=batch_ledger,
        report_job_ledger=ledger,
    )
    batch_scheduler = ReportBatchScheduler(
        batch_ledger=batch_ledger,
        portfolio_source=ProofPortfolioSource(),
    )

    report_app.dependency_overrides[get_report_job_ledger] = lambda: ledger
    report_app.dependency_overrides[get_report_lineage_store] = lambda: lineage_store
    report_app.dependency_overrides[get_report_batch_ledger] = lambda: batch_ledger
    report_app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        capture_service
    )
    report_app.dependency_overrides[get_portfolio_review_render_orchestration_service] = lambda: (
        render_service
    )
    report_app.dependency_overrides[get_portfolio_review_rerender_service] = lambda: (
        rerender_service
    )
    report_app.dependency_overrides[get_portfolio_review_regenerate_service] = lambda: (
        regenerate_service
    )
    report_app.dependency_overrides[get_portfolio_review_replay_service] = lambda: replay_service
    report_app.dependency_overrides[get_report_batch_item_replay_service] = lambda: (
        batch_replay_service
    )
    report_app.dependency_overrides[get_report_batch_scheduler] = lambda: batch_scheduler
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
