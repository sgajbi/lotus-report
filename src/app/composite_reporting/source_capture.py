"""Bounded source capture seam for the existing immutable Report lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol

from pydantic import ValidationError

from app.composite_reporting.admission import (
    CompositeEvidenceRefused,
    admit_composite_response,
)
from app.composite_reporting.models import CompositeReportSelection
from app.composite_reporting.semantic_contract import CompositeReportData
from app.composite_reporting.table_builder import build_composite_tables
from app.observability import bind_propagation_context
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_lineage.capture_service import (
    PortfolioReviewInputCapture,
    PortfolioReviewInputCaptureError,
    _RecordedUpstreamCall,
    _UpstreamRecorder,
)


class CompositePerformanceClient(Protocol):
    async def get_composite_twr(
        self, payload: dict[str, Any], *, admitted_tenant_id: str
    ) -> tuple[int, dict[str, Any]]: ...


class CompositeInputProvider:
    def __init__(self, *, performance_client: CompositePerformanceClient) -> None:
        self._performance_client = performance_client

    async def collect_for_job(self, job: ReportJobLedgerRecord) -> PortfolioReviewInputCapture:
        calls: list[_RecordedUpstreamCall] = []
        try:
            try:
                selection = CompositeReportSelection.model_validate(
                    job.options.get("composite_selection")
                )
            except ValidationError as exc:
                raise CompositeEvidenceRefused("COMPOSITE_REPORT_SELECTION_INVALID") from exc
            if (
                job.report_type != "composite_review"
                or selection.tenant_id != job.tenant_id
                or job.portfolio_scope != {"composite_id": selection.composite_id}
                or job.as_of_date != selection.period_end
                or job.reporting_currency != selection.reporting_currency
            ):
                raise CompositeEvidenceRefused("COMPOSITE_REPORT_JOB_IDENTITY_MISMATCH")
            started = perf_counter()
            try:
                with bind_propagation_context(
                    correlation_id=job.correlation_id, trace_id=job.trace_id
                ):
                    status, response = await self._performance_client.get_composite_twr(
                        selection.performance_request(), admitted_tenant_id=job.tenant_id
                    )
            except Exception as exc:
                recorder = _UpstreamRecorder(
                    correlation_id=job.correlation_id, trace_id=job.trace_id
                )
                recorder.append_failure(
                    service_name="lotus-performance",
                    endpoint="/composites/twr",
                    method="POST",
                    request_payload=selection.performance_request(),
                    started_at=started,
                    exc=exc,
                )
                calls.extend(recorder.calls)
                raise
            call = _source_call(job, selection, status, response, started)
            calls.append(call)
            dataset = admit_composite_response(
                selection=selection,
                admitted_tenant_id=job.tenant_id,
                status_code=status,
                payload=response,
            )
            dataset = build_composite_tables(dataset)
            CompositeReportData.model_validate(dataset)
            if response["status"] != "READY":
                call.supportability_status = "partial"
                call.completeness_status = "partial"
                call.failure_category = "partial_data"
                call.failure_message = "Composite source retains unavailable financial evidence."
            return PortfolioReviewInputCapture(snapshot_payload=dataset, upstream_calls=calls)
        except Exception as exc:
            # A response refused by semantic admission must not retain HTTP200
            # as complete lineage. Failure preserves its response digest, not a
            # plausible empty successful financial snapshot.
            if calls and calls[-1].response_payload is not None:
                calls[-1].supportability_status = "error"
                calls[-1].completeness_status = "error"
                calls[-1].failure_category = "upstream_error"
                calls[-1].failure_message = "Pinned composite source evidence failed admission."
            raise PortfolioReviewInputCaptureError(
                original_error=exc, upstream_calls=calls
            ) from exc


def _source_call(
    job: ReportJobLedgerRecord,
    selection: CompositeReportSelection,
    status: int,
    response: dict[str, Any],
    started: float,
) -> _RecordedUpstreamCall:
    return _RecordedUpstreamCall(
        service_name="lotus-performance",
        endpoint="/composites/twr",
        method="POST",
        contract_version="composite-twr.explicit-retained-selection.v1",
        request_payload=selection.performance_request(),
        response_payload=response,
        response_ref=str(selection.calculation_id),
        status_code=status,
        latency_ms=max(0, int((perf_counter() - started) * 1000)),
        supportability_status="complete",
        completeness_status="complete",
        failure_category="none",
        failure_message=None,
        captured_at=datetime.now(UTC),
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
    )
