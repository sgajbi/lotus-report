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
from app.composite_reporting.models import CompositeReportSelection, CompositeReviewJobRequest
from app.composite_reporting.product_contract import (
    CapturedReturnProduct,
    validate_composite_dataset,
)
from app.composite_reporting.product_tables import build_product_tables
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
                request = CompositeReviewJobRequest.model_validate(
                    {
                        "selection": job.options.get("composite_selection"),
                        "source_products": job.options.get("composite_source_products"),
                    }
                )
                selection = request.selection
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
            dataset = await self._capture_selection(job, selection, calls)
            dataset = build_composite_tables(dataset)
            products = []
            for product in request.source_products or []:
                captured = await self._capture_selection(job, product.selection, calls)
                products.append(
                    CapturedReturnProduct(
                        pin=product,
                        endpoint="/composites/twr",
                        method="POST",
                        source_response_digest=captured["source_response_digest"],
                        source_response=captured["source_response"],
                    )
                )
            if products:
                dataset = build_product_tables(dataset, products)
            validate_composite_dataset(dataset)
            accepted = (job.accepted_document_contract or {}).get("input_snapshot_contract_version")
            if accepted is not None and accepted != dataset["contract_version"]:
                raise CompositeEvidenceRefused("COMPOSITE_REPORT_CAPTURE_CONTRACT_CONFLICT")
            return PortfolioReviewInputCapture(snapshot_payload=dataset, upstream_calls=calls)
        except Exception as exc:
            # A refused HTTP200 must never retain complete successful lineage.
            if calls and calls[-1].response_payload is not None:
                calls[-1].supportability_status = "error"
                calls[-1].completeness_status = "error"
                calls[-1].failure_category = "upstream_error"
                calls[-1].failure_message = "Pinned composite source evidence failed admission."
            raise PortfolioReviewInputCaptureError(
                original_error=exc, upstream_calls=calls
            ) from exc

    async def _capture_selection(
        self,
        job: ReportJobLedgerRecord,
        selection: CompositeReportSelection,
        calls: list[_RecordedUpstreamCall],
    ) -> dict[str, Any]:
        started = perf_counter()
        try:
            with bind_propagation_context(correlation_id=job.correlation_id, trace_id=job.trace_id):
                status, response = await self._performance_client.get_composite_twr(
                    selection.performance_request(), admitted_tenant_id=job.tenant_id
                )
        except Exception as exc:
            recorder = _UpstreamRecorder(correlation_id=job.correlation_id, trace_id=job.trace_id)
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
        if response["status"] != "READY":
            call.supportability_status = "partial"
            call.completeness_status = "partial"
            call.failure_category = "partial_data"
            call.failure_message = "Composite source retains unavailable financial evidence."
        return dataset


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
