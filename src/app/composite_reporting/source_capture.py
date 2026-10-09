"""Bounded source capture seam for the existing immutable Report lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol
from urllib.parse import quote

from pydantic import ValidationError

from app.composite_reporting.admission import (
    CompositeEvidenceRefused,
    admit_composite_response,
)
from app.composite_reporting.eligibility_tables import build_eligibility_dataset
from app.composite_reporting.linked_tables import build_linked_dataset
from app.composite_reporting.models import (
    AmendmentEligibilitySelection,
    CompositeReportSelection,
    CompositeReviewJobRequest,
    EligibilitySelection,
    LinkedAnalysisSelection,
    PooledAnalysisSelection,
    PublishedEligibilityPin,
)
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
    async def get_retained_composite_pooled_result(
        self, calculation_id: Any, *, admitted_tenant_id: str
    ) -> tuple[int, dict[str, Any]]: ...

    async def get_composite_twr(
        self, payload: dict[str, Any], *, admitted_tenant_id: str
    ) -> tuple[int, dict[str, Any]]: ...

    async def get_composite_analytics(
        self, payload: dict[str, Any], *, admitted_tenant_id: str
    ) -> tuple[int, dict[str, Any]]: ...


class CompositeManageClient(Protocol):
    async def read_eligibility(
        self,
        *,
        endpoint: str,
        binding: dict[str, Any] | None,
        admitted_tenant_id: str,
    ) -> tuple[int, dict[str, Any]]: ...


class CompositeInputProvider:
    def __init__(
        self,
        *,
        performance_client: CompositePerformanceClient | None = None,
        manage_client: CompositeManageClient | None = None,
    ) -> None:
        self._performance_client = performance_client
        self._manage_client = manage_client

    async def collect_for_job(self, job: ReportJobLedgerRecord) -> PortfolioReviewInputCapture:
        calls: list[_RecordedUpstreamCall] = []
        try:
            try:
                request = CompositeReviewJobRequest.model_validate(
                    {
                        "selection": job.options.get("composite_selection"),
                        "linked_selection": job.options.get("composite_linked_selection"),
                        "eligibility_selection": job.options.get("composite_eligibility_selection"),
                        "pooled_selection": job.options.get("composite_pooled_selection"),
                        "source_products": job.options.get("composite_source_products"),
                    }
                )
                selection = request.primary_selection
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
            if request.pooled_selection is not None:
                dataset = await self._capture_pooled(job, request.pooled_selection, calls)
            elif request.eligibility_selection is not None:
                dataset = await self._capture_eligibility(job, request.eligibility_selection, calls)
            elif request.linked_selection is not None:
                dataset = await self._capture_linked(job, request.linked_selection, calls)
            else:
                assert request.selection is not None
                dataset = await self._capture_selection(job, request.selection, calls)
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

    async def _capture_pooled(
        self,
        job: ReportJobLedgerRecord,
        selection: PooledAnalysisSelection,
        calls: list[_RecordedUpstreamCall],
    ) -> dict[str, Any]:
        from app.composite_reporting.pooled_contract import admit_pooled_response
        from app.composite_reporting.pooled_tables import build_pooled_dataset

        payload = await self._read_pooled(job, selection.calculation_id, calls)
        admit_pooled_response(
            selection=selection, admitted_tenant_id=job.tenant_id, status_code=200, payload=payload
        )
        predecessor = None
        if selection.correction_of_calculation_id is not None:
            predecessor = await self._read_pooled(
                job, selection.correction_of_calculation_id, calls
            )
        if (
            sum(
                len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
                for value in (payload, predecessor)
                if value is not None
            )
            > 8_388_608
        ):
            raise CompositeEvidenceRefused("COMPOSITE_POOLED_CAPTURE_CAPACITY_EXCEEDED")
        return build_pooled_dataset(
            selection=selection,
            admitted_tenant_id=job.tenant_id,
            status_code=200,
            payload=payload,
            predecessor=predecessor,
        )

    async def _read_pooled(
        self,
        job: ReportJobLedgerRecord,
        calculation_id: Any,
        calls: list[_RecordedUpstreamCall],
    ) -> dict[str, Any]:
        endpoint = f"/performance/composites/analytics/results/{calculation_id}"
        started = perf_counter()
        assert self._performance_client is not None
        try:
            with bind_propagation_context(correlation_id=job.correlation_id, trace_id=job.trace_id):
                (
                    status,
                    response,
                ) = await self._performance_client.get_retained_composite_pooled_result(
                    calculation_id, admitted_tenant_id=job.tenant_id
                )
        except Exception as exc:
            recorder = _UpstreamRecorder(correlation_id=job.correlation_id, trace_id=job.trace_id)
            recorder.append_failure(
                service_name="lotus-performance",
                endpoint=endpoint,
                method="GET",
                request_payload={},
                started_at=started,
                exc=exc,
            )
            calls.extend(recorder.calls)
            raise
        calls.append(
            _RecordedUpstreamCall(
                service_name="lotus-performance",
                endpoint=endpoint,
                method="GET",
                contract_version="composite-pooled-mwr.v1",
                request_payload={},
                response_payload=response,
                response_ref=str(calculation_id),
                status_code=status,
                latency_ms=max(0, int((perf_counter() - started) * 1000)),
                supportability_status="partial",
                completeness_status="partial",
                failure_category="partial_data",
                failure_message="Source authority is not attested.",
                captured_at=datetime.now(UTC),
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        )
        if status != 200:
            raise CompositeEvidenceRefused("COMPOSITE_REPORT_SOURCE_UNAVAILABLE")
        return response

    async def _capture_linked(
        self,
        job: ReportJobLedgerRecord,
        selection: LinkedAnalysisSelection,
        calls: list[_RecordedUpstreamCall],
    ) -> dict[str, Any]:
        started = perf_counter()
        assert self._performance_client is not None
        try:
            with bind_propagation_context(correlation_id=job.correlation_id, trace_id=job.trace_id):
                status, response = await self._performance_client.get_composite_analytics(
                    selection.performance_request(), admitted_tenant_id=job.tenant_id
                )
        except Exception as exc:
            recorder = _UpstreamRecorder(correlation_id=job.correlation_id, trace_id=job.trace_id)
            recorder.append_failure(
                service_name="lotus-performance",
                endpoint="/composites/analytics",
                method="POST",
                request_payload=selection.performance_request(),
                started_at=started,
                exc=exc,
            )
            calls.extend(recorder.calls)
            raise
        calls.append(
            _source_call(
                job, selection, status, response, started, endpoint="/composites/analytics"
            )
        )
        return build_linked_dataset(
            selection=selection,
            admitted_tenant_id=job.tenant_id,
            status_code=status,
            payload=response,
        )

    async def _capture_selection(
        self,
        job: ReportJobLedgerRecord,
        selection: CompositeReportSelection,
        calls: list[_RecordedUpstreamCall],
    ) -> dict[str, Any]:
        started = perf_counter()
        assert self._performance_client is not None
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

    async def _capture_eligibility(
        self,
        job: ReportJobLedgerRecord,
        selection: EligibilitySelection | AmendmentEligibilitySelection,
        calls: list[_RecordedUpstreamCall],
    ) -> dict[str, Any]:
        from app.composite_reporting.admission import response_digest
        from app.composite_reporting.eligibility_admission import require_hash

        captured_bytes = 0

        async def read(endpoint: str, binding: dict[str, Any] | None = None) -> dict[str, Any]:
            nonlocal captured_bytes
            response = await self._manage_read(job, endpoint, calls, binding)
            captured_bytes += len(
                json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            if captured_bytes > 8_388_608:
                raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_CAPTURE_CAPACITY_EXCEEDED")
            return response

        base = "/api/v1/rebalance/composites"
        definition = (
            f"{base}/{quote(selection.composite_id, safe='')}/definitions/"
            f"{quote(selection.definition_version, safe='')}"
        )
        months: list[dict[str, Any]] = []
        for pin in selection.months:
            month: dict[str, Any]
            if isinstance(pin, PublishedEligibilityPin):
                member_path = f"{definition}/membership/{quote(pin.membership_revision, safe='')}"
                member = await read(member_path)
                universe = await read(
                    (
                        f"{member_path}/universe-attestations/"
                        f"{quote(pin.attestation_version, safe='')}"
                    ),
                )
                require_hash(universe, recursive=True)
                locators = [
                    item
                    for item in universe.get("source_products", [])
                    if (
                        item.get("owner_service") == "lotus-manage"
                        and item.get("product_name") == "CompositeMonthlyEvaluationApproval"
                    )
                ]
                if len(locators) != 1:
                    raise CompositeEvidenceRefused(
                        "COMPOSITE_ELIGIBILITY_APPROVAL_LOCATOR_CONFLICT"
                    )
                locator = locators[0]
                binding = {
                    "product_name": locator["product_name"],
                    "product_version": locator["contract_version"],
                    "revision": locator["source_watermark"],
                    "digest": locator["content_hash"],
                }
                if binding != {
                    "product_name": "CompositeMonthlyEvaluationApproval",
                    "product_version": "v2"
                    if isinstance(selection, AmendmentEligibilitySelection)
                    else "v1",
                    "revision": pin.evaluation_revision,
                    "digest": pin.approval_content_hash,
                }:
                    raise CompositeEvidenceRefused(
                        "COMPOSITE_ELIGIBILITY_APPROVAL_LOCATOR_CONFLICT"
                    )
                month = {
                    "evidence_kind": "PUBLISHED",
                    "membership": member,
                    "universe": universe,
                    "receipt": await read(f"{definition}/eligibility-evidence/resolve", binding),
                    "parent_membership": await read(
                        f"{definition}/membership/{quote(pin.parent_membership_revision, safe='')}",
                    ),
                    "publication": await read(f"{base}/publications/{pin.publication_sequence}"),
                }
            else:
                month = {
                    "evidence_kind": "EVALUATED_ONLY",
                    "proposal": await read(
                        (
                            f"{definition}/monthly-eligibility/evaluations/"
                            f"{quote(pin.evaluation_revision, safe='')}"
                        ),
                    ),
                }
            if isinstance(selection, AmendmentEligibilitySelection):
                from app.composite_reporting.eligibility_contract import proposal_for_month

                proposal, _ = proposal_for_month(month)
                if isinstance(pin, PublishedEligibilityPin):
                    month["parent_publication"] = await read(
                        f"{base}/publications/{proposal['amendment']['expected_current_publication_sequence']}"
                    )
                month["lineage_receipts"] = [
                    await read(
                        f"{definition}/eligibility-evidence/resolve",
                        {
                            "product_name": "CompositeMonthlyEvaluationApproval",
                            "product_version": prior.product_version,
                            "revision": prior.evaluation_revision,
                            "digest": prior.approval_content_hash,
                        },
                    )
                    for prior in selection.months[len(months)].lineage_receipts
                ]
            month["response_digests"] = {
                key: response_digest(value)
                for key, value in month.items()
                if isinstance(value, dict)
            }
            months.append(month)
        if isinstance(selection, AmendmentEligibilitySelection):
            from app.composite_reporting.amendment_tables import build_amendment_dataset

            return build_amendment_dataset(selection, months)
        return build_eligibility_dataset(selection, months)

    async def _manage_read(
        self,
        job: ReportJobLedgerRecord,
        endpoint: str,
        calls: list[_RecordedUpstreamCall],
        binding: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = perf_counter()
        method = "GET" if binding is None else "POST"
        try:
            if self._manage_client is None:
                raise CompositeEvidenceRefused("COMPOSITE_MANAGE_READ_CLIENT_REQUIRED")
            with bind_propagation_context(correlation_id=job.correlation_id, trace_id=job.trace_id):
                status, response = await self._manage_client.read_eligibility(
                    endpoint=endpoint, binding=binding, admitted_tenant_id=job.tenant_id
                )
        except Exception as exc:
            recorder = _UpstreamRecorder(correlation_id=job.correlation_id, trace_id=job.trace_id)
            recorder.append_failure(
                service_name="lotus-manage",
                endpoint=endpoint,
                method=method,
                request_payload=binding or {},
                started_at=started,
                exc=exc,
            )
            calls.extend(recorder.calls)
            raise
        calls.append(
            _RecordedUpstreamCall(
                service_name="lotus-manage",
                endpoint=endpoint,
                method=method,
                contract_version="composite-eligibility.exact-custody.v1",
                request_payload=binding or {},
                response_payload=response,
                response_ref=response.get("content_hash"),
                status_code=status,
                latency_ms=max(0, int((perf_counter() - started) * 1000)),
                supportability_status="partial",
                completeness_status="partial",
                failure_category="partial_data",
                failure_message="Controlled eligibility source is not attested.",
                captured_at=datetime.now(UTC),
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        )
        if status != 200:
            raise CompositeEvidenceRefused("COMPOSITE_ELIGIBILITY_SOURCE_UNAVAILABLE")
        return response


def _source_call(
    job: ReportJobLedgerRecord,
    selection: CompositeReportSelection | LinkedAnalysisSelection,
    status: int,
    response: dict[str, Any],
    started: float,
    *,
    endpoint: str = "/composites/twr",
) -> _RecordedUpstreamCall:
    return _RecordedUpstreamCall(
        service_name="lotus-performance",
        endpoint=endpoint,
        method="POST",
        contract_version="composite-linked.explicit-retained-selection.v1"
        if isinstance(selection, LinkedAnalysisSelection)
        else "composite-twr.explicit-retained-selection.v1",
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
