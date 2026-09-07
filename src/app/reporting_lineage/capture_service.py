from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter
from typing import Any, Protocol

import httpx

from app.application_errors import (
    ReportingNotFoundError,
    ReportingUpstreamError,
    ReportingValidationError,
)
from app.clients.ai_client import AiClient
from app.clients.core_query_client import CoreQueryClient
from app.clients.performance_client import PerformanceClient
from app.clients.risk_client import RiskClient
from app.config import settings
from app.report_ordering_catalogue.template_resolution import resolve_report_family
from app.reporting_identity.capture_binding import revision_for_capture
from app.reporting_identity.snapshot_lifecycle import snapshot_lifecycle_claim
from app.reporting_identity.source_cut_coherence import evaluate_source_cut_coherence
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_lineage.advisor_commentary import (
    AcceptedOutputClient,
    AdvisorCommentarySourceUnavailableError,
    advisor_commentary_requested,
    requested_advisor_brief_run_id,
    resolve_advisor_commentary_package,
)
from app.reporting_lineage.allocation_presentation import resolve_allocation_presentation
from app.reporting_lineage.models import (
    ReportInputSnapshotCreateRequest,
    ReportInputSnapshotRecord,
    ReportUpstreamCallCreateRequest,
    ReportUpstreamCallRecord,
    SnapshotPosture,
    UpstreamFailureCategory,
)
from app.reporting_lineage.store import (
    ReportInputSnapshotAlreadyCapturedError,
    ReportInputSnapshotLineageConflictError,
    ReportInputSnapshotNotFoundError,
    canonical_json_dumps,
)
from app.reporting_metrics import (
    record_advisor_commentary_resolution,
    record_report_operation,
)
from app.services.reporting_read_service import ReportingReadService


class ReportJobCaptureLedger(Protocol):
    def append_job_event(
        self,
        *,
        job_id: str,
        event_type: str,
        message: str,
        event_payload: dict[str, object] | None = None,
        event_idempotency_key: str | None = None,
        actor: str,
        correlation_id: str,
        trace_id: str,
        skip_if_idempotency_key_exists: bool = False,
    ) -> bool: ...

    def mark_collecting_data(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> ReportJobLedgerRecord: ...

    def mark_data_ready(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
    ) -> ReportJobLedgerRecord: ...

    def mark_failed(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        failure_category: str,
        failure_message: str,
        retry_eligible: bool,
    ) -> ReportJobLedgerRecord: ...


class ReportInputSnapshotStorePort(Protocol):
    def get_snapshot_by_job(self, report_job_id: str) -> ReportInputSnapshotRecord: ...

    def create_snapshot(
        self,
        request: ReportInputSnapshotCreateRequest,
    ) -> ReportInputSnapshotRecord: ...

    def create_upstream_calls(
        self,
        *,
        snapshot_id: str,
        calls: list[ReportUpstreamCallCreateRequest],
    ) -> list[Any]: ...

    def create_capture(
        self,
        *,
        snapshot: ReportInputSnapshotCreateRequest,
        upstream_calls: list[ReportUpstreamCallCreateRequest],
    ) -> tuple[ReportInputSnapshotRecord, list[ReportUpstreamCallRecord]]: ...

    def list_upstream_calls(self, snapshot_id: str) -> list[ReportUpstreamCallRecord]: ...


@dataclass(slots=True)
class _RecordedUpstreamCall:
    service_name: str
    endpoint: str
    method: str
    contract_version: str
    request_payload: dict[str, Any]
    response_payload: dict[str, Any] | None
    response_ref: str | None
    status_code: int
    latency_ms: int
    supportability_status: SnapshotPosture
    completeness_status: SnapshotPosture
    failure_category: UpstreamFailureCategory
    failure_message: str | None
    captured_at: datetime
    correlation_id: str
    trace_id: str

    def to_create_request(self) -> ReportUpstreamCallCreateRequest:
        response_hash = None
        if self.response_payload is not None:
            response_hash = _hash_payload(self.response_payload)
        return ReportUpstreamCallCreateRequest(
            service_name=self.service_name,
            endpoint=self.endpoint,
            method=self.method,
            contract_version=self.contract_version,
            request_hash=_hash_payload(self.request_payload),
            response_hash=response_hash,
            response_ref=self.response_ref,
            status_code=self.status_code,
            latency_ms=self.latency_ms,
            supportability_status=self.supportability_status,
            completeness_status=self.completeness_status,
            failure_category=self.failure_category,
            failure_message=self.failure_message,
            captured_at=self.captured_at,
            correlation_id=self.correlation_id,
            trace_id=self.trace_id,
        )


@dataclass(slots=True)
class PortfolioReviewInputCapture:
    snapshot_payload: dict[str, Any]
    upstream_calls: list[_RecordedUpstreamCall]


class PortfolioReviewInputCaptureError(RuntimeError):
    def __init__(
        self,
        *,
        original_error: Exception,
        upstream_calls: list[_RecordedUpstreamCall],
    ) -> None:
        super().__init__(str(original_error))
        self.original_error = original_error
        self.upstream_calls = upstream_calls


class PortfolioReviewInputProvider(Protocol):
    async def collect_for_job(
        self,
        job: ReportJobLedgerRecord,
    ) -> PortfolioReviewInputCapture: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _hash_payload(payload: dict[str, Any]) -> str:
    return f"sha256:{sha256(canonical_json_dumps(payload).encode('utf-8')).hexdigest()}"


def _payload_contract_version(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "unknown"
    version = payload.get("contract_version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return "v1"


def _payload_text(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    try:
        return str(canonical_json_dumps(payload)).lower()
    except Exception:
        return str(payload).lower()


def _classify_call(
    status_code: int,
    payload: dict[str, Any] | None,
) -> tuple[SnapshotPosture, SnapshotPosture, UpstreamFailureCategory, str | None]:
    payload_text = _payload_text(payload)
    if "redacted" in payload_text:
        return "redacted", "redacted", "redacted", "Upstream response content was redacted."
    if "not_supported" in payload_text or "unsupported" in payload_text:
        return (
            "not_supported",
            "not_supported",
            "unsupported_input",
            ("Upstream service reported that the requested input or capability is not supported."),
        )
    if status_code >= 500:
        return (
            "unavailable",
            "unavailable",
            "upstream_unavailable",
            ("Upstream service was unavailable while report data was being captured."),
        )
    if status_code >= 400:
        return (
            "error",
            "error",
            "upstream_error",
            ("Upstream service returned an error during report data capture."),
        )
    if (
        "partial" in payload_text
        or "missing_fields" in payload_text
        or "source_unavailable" in payload_text
        or "source_payload_missing" in payload_text
    ):
        return (
            "partial",
            "partial",
            "partial_data",
            ("Upstream response was accepted but only partially supportable."),
        )
    return "complete", "complete", "none", None


class _UpstreamRecorder:
    def __init__(self, *, correlation_id: str, trace_id: str):
        self._correlation_id = correlation_id
        self._trace_id = trace_id
        self._calls: list[_RecordedUpstreamCall] = []

    @property
    def calls(self) -> list[_RecordedUpstreamCall]:
        return list(self._calls)

    def append_success(
        self,
        *,
        service_name: str,
        endpoint: str,
        method: str,
        request_payload: dict[str, Any],
        status_code: int,
        response_payload: dict[str, Any],
        started_at: float,
    ) -> None:
        supportability, completeness, failure_category, failure_message = _classify_call(
            status_code, response_payload
        )
        self._calls.append(
            _RecordedUpstreamCall(
                service_name=service_name,
                endpoint=endpoint,
                method=method,
                contract_version=_payload_contract_version(response_payload),
                request_payload=request_payload,
                response_payload=response_payload,
                response_ref=(
                    "redacted:inline-hash-only" if supportability == "redacted" else None
                ),
                status_code=status_code,
                latency_ms=max(0, int((perf_counter() - started_at) * 1000)),
                supportability_status=supportability,
                completeness_status=completeness,
                failure_category=failure_category,
                failure_message=failure_message,
                captured_at=_utc_now(),
                correlation_id=self._correlation_id,
                trace_id=self._trace_id,
            )
        )

    def append_failure(
        self,
        *,
        service_name: str,
        endpoint: str,
        method: str,
        request_payload: dict[str, Any],
        started_at: float,
        exc: Exception,
    ) -> None:
        supportability: SnapshotPosture
        completeness: SnapshotPosture
        failure_category: UpstreamFailureCategory
        if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
            status_code = 504
            supportability = "unavailable"
            completeness = "unavailable"
            failure_category = "timeout"
            failure_message = "Upstream request timed out before a complete response was returned."
        else:
            status_code = 500
            supportability = "error"
            completeness = "error"
            failure_category = "upstream_error"
            failure_message = "Upstream request failed before a usable response was returned."
        self._calls.append(
            _RecordedUpstreamCall(
                service_name=service_name,
                endpoint=endpoint,
                method=method,
                contract_version="unknown",
                request_payload=request_payload,
                response_payload=None,
                response_ref=None,
                status_code=status_code,
                latency_ms=max(0, int((perf_counter() - started_at) * 1000)),
                supportability_status=supportability,
                completeness_status=completeness,
                failure_category=failure_category,
                failure_message=failure_message,
                captured_at=_utc_now(),
                correlation_id=self._correlation_id,
                trace_id=self._trace_id,
            )
        )


class _RecordingCoreQueryClient(CoreQueryClient):
    def __init__(self, inner: CoreQueryClient, recorder: _UpstreamRecorder):
        self._inner = inner
        self._recorder = recorder

    async def get_portfolio_summary(
        self, portfolio_id: str, payload: dict[str, Any], correlation_id: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        request_payload = {"portfolio_id": portfolio_id, **dict(payload)}
        return await self._record(
            service_name="lotus-core",
            endpoint="/reporting/portfolio-summary/query",
            method="POST",
            request_payload=request_payload,
            operation=lambda: self._inner.get_portfolio_summary(
                portfolio_id, payload, correlation_id
            ),
        )

    async def get_asset_allocation(
        self, portfolio_id: str, payload: dict[str, Any], correlation_id: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        request_payload = dict(payload)
        request_payload["scope"] = {"portfolio_id": portfolio_id}
        return await self._record(
            service_name="lotus-core",
            endpoint="/reporting/asset-allocation/query",
            method="POST",
            request_payload=request_payload,
            operation=lambda: self._inner.get_asset_allocation(
                portfolio_id, payload, correlation_id
            ),
        )

    async def get_portfolio_transactions(
        self, portfolio_id: str, params: dict[str, Any], correlation_id: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        return await self._record(
            service_name="lotus-core",
            endpoint=f"/portfolios/{portfolio_id}/transactions",
            method="GET",
            request_payload=dict(params),
            operation=lambda: self._inner.get_portfolio_transactions(
                portfolio_id, params, correlation_id
            ),
        )

    async def get_portfolio_positions(
        self, portfolio_id: str, params: dict[str, Any], correlation_id: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        return await self._record(
            service_name="lotus-core",
            endpoint=f"/portfolios/{portfolio_id}/positions",
            method="GET",
            request_payload=dict(params),
            operation=lambda: self._inner.get_portfolio_positions(
                portfolio_id, params, correlation_id
            ),
        )

    async def get_portfolio_detail(
        self, portfolio_id: str, correlation_id: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        return await self._record(
            service_name="lotus-core",
            endpoint=f"/portfolios/{portfolio_id}",
            method="GET",
            request_payload={"portfolio_id": portfolio_id},
            operation=lambda: self._inner.get_portfolio_detail(portfolio_id, correlation_id),
        )

    async def _record(
        self,
        *,
        service_name: str,
        endpoint: str,
        method: str,
        request_payload: dict[str, Any],
        operation: Any,
    ) -> tuple[int, dict[str, Any]]:
        started_at = perf_counter()
        try:
            status_code, response_payload = await operation()
        except Exception as exc:
            self._recorder.append_failure(
                service_name=service_name,
                endpoint=endpoint,
                method=method,
                request_payload=request_payload,
                started_at=started_at,
                exc=exc,
            )
            raise
        self._recorder.append_success(
            service_name=service_name,
            endpoint=endpoint,
            method=method,
            request_payload=request_payload,
            status_code=status_code,
            response_payload=response_payload,
            started_at=started_at,
        )
        return status_code, response_payload


class _RecordingPerformanceClient(PerformanceClient):
    def __init__(self, inner: PerformanceClient, recorder: _UpstreamRecorder):
        self._inner = inner
        self._recorder = recorder

    async def get_workspace_summary(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        started_at = perf_counter()
        try:
            status_code, response_payload = await self._inner.get_workspace_summary(payload)
        except Exception as exc:
            self._recorder.append_failure(
                service_name="lotus-performance",
                endpoint="/performance/workspace-summary",
                method="POST",
                request_payload=dict(payload),
                started_at=started_at,
                exc=exc,
            )
            raise
        self._recorder.append_success(
            service_name="lotus-performance",
            endpoint="/performance/workspace-summary",
            method="POST",
            request_payload=dict(payload),
            status_code=status_code,
            response_payload=response_payload,
            started_at=started_at,
        )
        return status_code, response_payload

    async def get_contribution(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        started_at = perf_counter()
        try:
            status_code, response_payload = await self._inner.get_contribution(payload)
        except Exception as exc:
            self._recorder.append_failure(
                service_name="lotus-performance",
                endpoint="/performance/contribution",
                method="POST",
                request_payload=dict(payload),
                started_at=started_at,
                exc=exc,
            )
            raise
        self._recorder.append_success(
            service_name="lotus-performance",
            endpoint="/performance/contribution",
            method="POST",
            request_payload=dict(payload),
            status_code=status_code,
            response_payload=response_payload,
            started_at=started_at,
        )
        return status_code, response_payload


class _RecordingAiClient:
    """Records the accepted-output read as durable upstream-call evidence.

    Wraps any AcceptedOutputClient (production AiClient or an injected test
    double) so the lotus-ai read is always part of the snapshot's lineage.
    """

    def __init__(self, inner: AcceptedOutputClient, recorder: _UpstreamRecorder):
        self._inner = inner
        self._recorder = recorder

    async def get_accepted_workflow_output(
        self, run_id: str, *, tenant_id: str
    ) -> tuple[int, dict[str, Any]]:
        started_at = perf_counter()
        endpoint = "/platform/workflow-packs/runs/{run_id}/accepted-output"
        request_payload = {"run_id": run_id}
        try:
            status_code, response_payload = await self._inner.get_accepted_workflow_output(
                run_id, tenant_id=tenant_id
            )
        except Exception as exc:
            self._recorder.append_failure(
                service_name="lotus-ai",
                endpoint=endpoint,
                method="GET",
                request_payload=request_payload,
                started_at=started_at,
                exc=exc,
            )
            raise
        self._recorder.append_success(
            service_name="lotus-ai",
            endpoint=endpoint,
            method="GET",
            request_payload=request_payload,
            status_code=status_code,
            response_payload=response_payload,
            started_at=started_at,
        )
        return status_code, response_payload


class _RecordingRiskClient(RiskClient):
    def __init__(self, inner: RiskClient, recorder: _UpstreamRecorder):
        self._inner = inner
        self._recorder = recorder

    async def historical_attribution(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        started_at = perf_counter()
        try:
            status_code, response_payload = await self._inner.historical_attribution(payload)
        except Exception as exc:
            self._recorder.append_failure(
                service_name="lotus-risk",
                endpoint="/analytics/risk/historical-attribution",
                method="POST",
                request_payload=dict(payload),
                started_at=started_at,
                exc=exc,
            )
            raise
        self._recorder.append_success(
            service_name="lotus-risk",
            endpoint="/analytics/risk/historical-attribution",
            method="POST",
            request_payload=dict(payload),
            status_code=status_code,
            response_payload=response_payload,
            started_at=started_at,
        )
        return status_code, response_payload

    async def drawdown_analytics(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        started_at = perf_counter()
        try:
            status_code, response_payload = await self._inner.drawdown_analytics(payload)
        except Exception as exc:
            self._recorder.append_failure(
                service_name="lotus-risk",
                endpoint="/analytics/risk/drawdown",
                method="POST",
                request_payload=dict(payload),
                started_at=started_at,
                exc=exc,
            )
            raise
        self._recorder.append_success(
            service_name="lotus-risk",
            endpoint="/analytics/risk/drawdown",
            method="POST",
            request_payload=dict(payload),
            status_code=status_code,
            response_payload=response_payload,
            started_at=started_at,
        )
        return status_code, response_payload

    async def rolling_metrics(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        started_at = perf_counter()
        try:
            status_code, response_payload = await self._inner.rolling_metrics(payload)
        except Exception as exc:
            self._recorder.append_failure(
                service_name="lotus-risk",
                endpoint="/analytics/risk/rolling-metrics",
                method="POST",
                request_payload=dict(payload),
                started_at=started_at,
                exc=exc,
            )
            raise
        self._recorder.append_success(
            service_name="lotus-risk",
            endpoint="/analytics/risk/rolling-metrics",
            method="POST",
            request_payload=dict(payload),
            status_code=status_code,
            response_payload=response_payload,
            started_at=started_at,
        )
        return status_code, response_payload

    async def calculate_risk(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        started_at = perf_counter()
        try:
            status_code, response_payload = await self._inner.calculate_risk(payload)
        except Exception as exc:
            self._recorder.append_failure(
                service_name="lotus-risk",
                endpoint="/analytics/risk/calculate",
                method="POST",
                request_payload=dict(payload),
                started_at=started_at,
                exc=exc,
            )
            raise
        self._recorder.append_success(
            service_name="lotus-risk",
            endpoint="/analytics/risk/calculate",
            method="POST",
            request_payload=dict(payload),
            status_code=status_code,
            response_payload=response_payload,
            started_at=started_at,
        )
        return status_code, response_payload


class ReportingReadPortfolioReviewInputProvider:
    def __init__(
        self,
        *,
        read_service: ReportingReadService | None = None,
        ai_client: AcceptedOutputClient | None = None,
    ) -> None:
        self._read_service = read_service
        self._ai_client = ai_client

    async def collect_for_job(
        self,
        job: ReportJobLedgerRecord,
    ) -> PortfolioReviewInputCapture:
        recorder = _UpstreamRecorder(
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        read_service = self._read_service or ReportingReadService(
            core_query_client=_RecordingCoreQueryClient(
                CoreQueryClient(
                    base_url=settings.core_query_base_url,
                    timeout_seconds=settings.upstream_timeout_seconds,
                    max_retries=settings.upstream_max_retries,
                    retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
                ),
                recorder,
            ),
            performance_client=_RecordingPerformanceClient(
                PerformanceClient(
                    base_url=settings.performance_base_url,
                    timeout_seconds=settings.upstream_timeout_seconds,
                    max_retries=settings.upstream_max_retries,
                    retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
                ),
                recorder,
            ),
            risk_client=_RecordingRiskClient(
                RiskClient(
                    base_url=settings.risk_base_url,
                    timeout_seconds=settings.upstream_timeout_seconds,
                    max_retries=settings.upstream_max_retries,
                    retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
                ),
                recorder,
            ),
        )
        try:
            snapshot_payload = await read_service.get_portfolio_review(
                portfolio_id=_first_portfolio_id(job),
                request_payload=_request_payload(job),
                correlation_id=job.correlation_id or None,
                admitted_tenant_id=job.tenant_id,
                evidence_posture="durable_snapshot",
            )
        except Exception as exc:
            raise PortfolioReviewInputCaptureError(
                original_error=exc,
                upstream_calls=recorder.calls,
            ) from exc
        # The document's allocation-dimension set is a Report decision, so the
        # RESOLVED selection (caller's request, else default policy) belongs in
        # the immutable snapshot: without it, "which dimensions did this
        # document present?" is only answerable by replaying today's defaulting
        # policy against an old order (issue #224).
        snapshot_payload = dict(snapshot_payload)
        snapshot_payload["allocation_presentation"] = resolve_allocation_presentation(
            options=job.options,
            snapshot=snapshot_payload,
        )
        if advisor_commentary_requested(job.options):
            snapshot_payload = dict(snapshot_payload)
            snapshot_payload["advisor_commentary_package"] = await self._resolve_advisor_commentary(
                job=job,
                recorder=recorder,
                effective_reporting_currency=_optional_str(
                    snapshot_payload.get("reportingCurrency")
                )
                or job.reporting_currency,
                report_period=_report_period_label(snapshot_payload),
            )
        return PortfolioReviewInputCapture(
            snapshot_payload=snapshot_payload,
            upstream_calls=recorder.calls,
        )

    async def _resolve_advisor_commentary(
        self,
        *,
        job: ReportJobLedgerRecord,
        recorder: _UpstreamRecorder,
        effective_reporting_currency: str | None,
        report_period: str | None = None,
    ) -> dict[str, Any]:
        run_id = requested_advisor_brief_run_id(job.options)
        if run_id is None:
            # Acceptance validation requires the run id with the section; a
            # ledger row without one is a malformed legacy order and the
            # section closes rather than choosing a brief implicitly.
            return {
                "status": "unavailable",
                "reason_code": "advisor_brief_not_found",
                "advisor_brief_run_id": None,
                "detail": "The order named the section without an accepted brief run id.",
            }
        ai_client = _RecordingAiClient(
            self._ai_client
            or AiClient(
                base_url=settings.ai_base_url,
                timeout_seconds=settings.upstream_timeout_seconds,
                max_retries=settings.upstream_max_retries,
                retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
            ),
            recorder,
        )
        try:
            return await resolve_advisor_commentary_package(
                client=ai_client,
                run_id=run_id,
                tenant_id=job.tenant_id,
                portfolio_id=_first_portfolio_id(job),
                as_of_date=job.as_of_date.isoformat(),
                reporting_currency=effective_reporting_currency,
                report_period=report_period,
                benchmark_code=_optional_str(job.options.get("benchmark_code")),
            )
        except AdvisorCommentarySourceUnavailableError as exc:
            raise PortfolioReviewInputCaptureError(
                original_error=exc,
                upstream_calls=recorder.calls,
            ) from exc


class PortfolioReviewSnapshotCaptureService:
    def __init__(
        self,
        *,
        snapshot_store: ReportInputSnapshotStorePort,
        job_ledger: ReportJobCaptureLedger,
        portfolio_review_input_provider: PortfolioReviewInputProvider | None = None,
    ) -> None:
        self._snapshot_store = snapshot_store
        self._job_ledger = job_ledger
        self._portfolio_review_input_provider = (
            portfolio_review_input_provider or ReportingReadPortfolioReviewInputProvider()
        )

    async def capture_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
        started_at = perf_counter()
        if job.report_type == "proof_pack":
            return self._capture_proof_pack_snapshot(job=job, started_at=started_at)
        if job.report_type == "outcome_review":
            return self._capture_outcome_review_snapshot(job=job, started_at=started_at)
        if job.report_type == "rebalance_wave":
            return self._capture_wave_snapshot(job=job, started_at=started_at)
        if job.status in {
            "data_ready",
            "failed",
            "cancelled",
            "completed",
            "completed_with_warnings",
        }:
            return job
        resumed = self._resume_existing_capture(job=job, started_at=started_at)
        if resumed is not None:
            return resumed

        if job.status == "accepted":
            self._job_ledger.mark_collecting_data(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        upstream_calls: list[_RecordedUpstreamCall] = []
        failure_message = None
        failure_category = "upstream_data_failed"
        retry_eligible = True
        try:
            input_capture = await self._portfolio_review_input_provider.collect_for_job(job)
            snapshot_payload = input_capture.snapshot_payload
            upstream_calls = input_capture.upstream_calls
            proposal_narrative_package = _proposal_narrative_package(job)
            if proposal_narrative_package is not None:
                snapshot_payload = dict(snapshot_payload)
                snapshot_payload["proposal_narrative_package"] = proposal_narrative_package
            proposal_memo_package = _proposal_memo_package(job)
            if proposal_memo_package is not None:
                # The exact accepted package, injected verbatim: the render
                # semantic reads it from the immutable snapshot, never from
                # the live request, and nothing rewrites or infers content.
                snapshot_payload = dict(snapshot_payload)
                snapshot_payload["proposal_memo_package"] = proposal_memo_package
        except PortfolioReviewInputCaptureError as exc:
            upstream_calls = exc.upstream_calls
            failure_category, failure_message, retry_eligible = _map_job_failure(exc.original_error)
            snapshot_payload = {
                "report_id": (
                    f"portfolio-review:{_first_portfolio_id(job)}:{job.as_of_date.isoformat()}"
                ),
                "portfolio_id": _first_portfolio_id(job),
                "as_of_date": job.as_of_date.isoformat(),
                "capture_status": "failed",
                "failure_category": failure_category,
                "failure_message": failure_message,
            }
        except Exception as exc:
            failure_category, failure_message, retry_eligible = _map_job_failure(exc)
            snapshot_payload = {
                "report_id": (
                    f"portfolio-review:{_first_portfolio_id(job)}:{job.as_of_date.isoformat()}"
                ),
                "portfolio_id": _first_portfolio_id(job),
                "as_of_date": job.as_of_date.isoformat(),
                "capture_status": "failed",
                "failure_category": failure_category,
                "failure_message": failure_message,
            }

        snapshot_request = ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version=_accepted_snapshot_contract_version(job),
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload=snapshot_payload,
            snapshot_storage_ref=None,
            supportability_status=_overall_posture(upstream_calls),
            completeness_status=_overall_posture(upstream_calls),
            lineage_summary=_lineage_summary(
                upstream_calls,
                proposal_narrative_package=_proposal_narrative_package(job),
                proposal_memo_package=_proposal_memo_package(job),
                advisor_commentary_package=snapshot_payload.get("advisor_commentary_package"),
            ),
            captured_at=_utc_now(),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        completed = self._complete_capture(
            job=job,
            started_at=started_at,
            snapshot_request=snapshot_request,
            upstream_calls=[call.to_create_request() for call in upstream_calls],
            failure_category=failure_category if failure_message else None,
            failure_message=failure_message,
            retry_eligible=retry_eligible,
        )
        self._record_advisor_commentary_closure(job=job, snapshot_payload=snapshot_payload)
        return completed

    def _record_advisor_commentary_closure(
        self,
        *,
        job: ReportJobLedgerRecord,
        snapshot_payload: dict[str, Any],
    ) -> None:
        package = snapshot_payload.get("advisor_commentary_package")
        if not isinstance(package, dict):
            return
        status_value = str(package.get("status") or "")
        if status_value == "included":
            record_advisor_commentary_resolution(outcome="included", reason_code=None)
            return
        if status_value != "unavailable":
            return
        reason_code = str(package.get("reason_code") or "advisor_brief_not_found")
        record_advisor_commentary_resolution(outcome="unavailable", reason_code=reason_code)
        self._job_ledger.append_job_event(
            job_id=job.job_id,
            event_type="job_advisor_commentary_unavailable",
            message=(
                "The requested ADVISOR_COMMENTARY section was closed with reason "
                f"{reason_code}; the report renders without it."
            ),
            event_payload={
                "reason_code": reason_code,
                "advisor_brief_run_id": package.get("advisor_brief_run_id"),
            },
            event_idempotency_key=f"job_advisor_commentary_unavailable:{job.job_id}",
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            skip_if_idempotency_key_exists=True,
        )

    def _capture_proof_pack_snapshot(
        self,
        *,
        job: ReportJobLedgerRecord,
        started_at: float,
    ) -> ReportJobLedgerRecord:
        if job.status in {
            "data_ready",
            "rendering",
            "completed",
            "archiving",
            "archived",
            "failed",
            "cancelled",
            "completed_with_warnings",
        }:
            return job
        resumed = self._resume_existing_capture(job=job, started_at=started_at)
        if resumed is not None:
            return resumed

        if job.status == "accepted":
            self._job_ledger.mark_collecting_data(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        proof_pack_report_input = job.options.get("proof_pack_report_input")
        if not isinstance(proof_pack_report_input, dict):
            failed_job = self._job_ledger.mark_failed(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
                failure_category="validation_failed",
                failure_message="Proof-pack report input was not present in the report job.",
                retry_eligible=False,
            )
            record_report_operation(
                operation="snapshot_capture",
                status=failed_job.status,
                failure_category=failed_job.failure_category,
                duration_seconds=perf_counter() - started_at,
            )
            return failed_job

        source_ref = _as_dict(proof_pack_report_input.get("evidence_ref"))
        source_type = _optional_str(source_ref.get("source_type")) or "DPM_PROOF_PACK_REPORT_INPUT"
        source_system = _proof_pack_source_system(
            source_system=_optional_str(source_ref.get("source_system")),
            source_type=source_type,
        )
        source_id = _optional_str(source_ref.get("source_id")) or str(
            proof_pack_report_input.get("proof_pack_id") or job.job_id
        )
        source_endpoint = _proof_pack_source_endpoint(source_system)
        source_contract_version = _proof_pack_source_contract_version(source_system)
        source_method = _proof_pack_source_method(source_system)

        snapshot_request = ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version=_accepted_snapshot_contract_version(job),
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload=proof_pack_report_input,
            snapshot_storage_ref=None,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={
                "source_services": [source_system],
                "call_count": 1,
                "supportability_status": "complete",
                "completeness_status": "complete",
                "proof_pack_id": proof_pack_report_input.get("proof_pack_id"),
                "source_type": source_type,
                "source_hash": proof_pack_report_input.get("content_hash"),
                **_portfolio_memory_lineage_summary(proof_pack_report_input),
            },
            captured_at=_utc_now(),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        source_hash = _required_sha256(
            proof_pack_report_input,
            "content_hash",
            "proof_pack_report_input",
        )
        return self._complete_capture(
            job=job,
            started_at=started_at,
            snapshot_request=snapshot_request,
            upstream_calls=[
                ReportUpstreamCallCreateRequest(
                    service_name=source_system,
                    endpoint=source_endpoint,
                    method=source_method,
                    contract_version=source_contract_version,
                    request_hash=_required_sha256(
                        proof_pack_report_input,
                        "proof_pack_content_hash",
                        "proof_pack_report_input",
                    ),
                    response_hash=source_hash,
                    response_ref=source_id,
                    status_code=200,
                    latency_ms=0,
                    supportability_status="complete",
                    completeness_status="complete",
                    failure_category="none",
                    failure_message=None,
                    captured_at=_utc_now(),
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                )
            ],
        )

    def _capture_outcome_review_snapshot(
        self,
        *,
        job: ReportJobLedgerRecord,
        started_at: float,
    ) -> ReportJobLedgerRecord:
        if job.status in {
            "data_ready",
            "rendering",
            "completed",
            "archiving",
            "archived",
            "failed",
            "cancelled",
            "completed_with_warnings",
        }:
            return job
        resumed = self._resume_existing_capture(job=job, started_at=started_at)
        if resumed is not None:
            return resumed

        if job.status == "accepted":
            self._job_ledger.mark_collecting_data(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        outcome_report_input = job.options.get("outcome_report_input")
        if not isinstance(outcome_report_input, dict):
            failed_job = self._job_ledger.mark_failed(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
                failure_category="validation_failed",
                failure_message="Outcome-review report input was not present in the report job.",
                retry_eligible=False,
            )
            record_report_operation(
                operation="snapshot_capture",
                status=failed_job.status,
                failure_category=failed_job.failure_category,
                duration_seconds=perf_counter() - started_at,
            )
            return failed_job

        snapshot_request = ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version=_accepted_snapshot_contract_version(job),
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload=outcome_report_input,
            snapshot_storage_ref=None,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={
                "source_services": ["lotus-manage"],
                "call_count": 1,
                "supportability_status": "complete",
                "completeness_status": "complete",
                "outcome_review_id": outcome_report_input.get("outcome_review_id"),
                "source_hash": outcome_report_input.get("content_hash"),
                **_portfolio_memory_lineage_summary(outcome_report_input),
            },
            captured_at=_utc_now(),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        source_hash = _required_sha256(
            outcome_report_input,
            "content_hash",
            "outcome_report_input",
        )
        return self._complete_capture(
            job=job,
            started_at=started_at,
            snapshot_request=snapshot_request,
            upstream_calls=[
                ReportUpstreamCallCreateRequest(
                    service_name="lotus-manage",
                    endpoint="/api/v1/rebalance/outcome-reviews/{outcome_review_id}/report-input",
                    method="GET",
                    contract_version="DpmOutcomeReportInput.1.0",
                    request_hash=_required_sha256(
                        outcome_report_input,
                        "outcome_review_content_hash",
                        "outcome_report_input",
                    ),
                    response_hash=source_hash,
                    response_ref=str(
                        outcome_report_input.get("outcome_review_id")
                        or job.options.get("outcome_review_id")
                        or job.job_id
                    ),
                    status_code=200,
                    latency_ms=0,
                    supportability_status="complete",
                    completeness_status="complete",
                    failure_category="none",
                    failure_message=None,
                    captured_at=_utc_now(),
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                )
            ],
        )

    def _capture_wave_snapshot(
        self,
        *,
        job: ReportJobLedgerRecord,
        started_at: float,
    ) -> ReportJobLedgerRecord:
        if job.status in {
            "data_ready",
            "rendering",
            "completed",
            "archiving",
            "archived",
            "failed",
            "cancelled",
            "completed_with_warnings",
        }:
            return job
        resumed = self._resume_existing_capture(job=job, started_at=started_at)
        if resumed is not None:
            return resumed

        if job.status == "accepted":
            self._job_ledger.mark_collecting_data(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
            )
        wave_report_input = job.options.get("wave_report_input")
        if not isinstance(wave_report_input, dict):
            failed_job = self._job_ledger.mark_failed(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
                failure_category="validation_failed",
                failure_message="Wave report input was not present in the report job.",
                retry_eligible=False,
            )
            record_report_operation(
                operation="snapshot_capture",
                status=failed_job.status,
                failure_category=failed_job.failure_category,
                duration_seconds=perf_counter() - started_at,
            )
            return failed_job

        snapshot_request = ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version=_accepted_snapshot_contract_version(job),
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload=wave_report_input,
            snapshot_storage_ref=None,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={
                "source_services": ["lotus-manage"],
                "call_count": 1,
                "supportability_status": "complete",
                "completeness_status": "complete",
                "wave_id": wave_report_input.get("wave_id"),
                "source_hash": wave_report_input.get("content_hash"),
                **_portfolio_memory_lineage_summary(wave_report_input),
            },
            captured_at=_utc_now(),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        source_hash = _required_sha256(wave_report_input, "content_hash", "wave_report_input")
        return self._complete_capture(
            job=job,
            started_at=started_at,
            snapshot_request=snapshot_request,
            upstream_calls=[
                ReportUpstreamCallCreateRequest(
                    service_name="lotus-manage",
                    endpoint="/api/v1/rebalance/waves/{wave_id}/report-input",
                    method="GET",
                    contract_version="DpmWaveReportInput.1.0",
                    request_hash=_required_sha256(
                        wave_report_input,
                        "wave_content_hash",
                        "wave_report_input",
                    ),
                    response_hash=source_hash,
                    response_ref=str(
                        wave_report_input.get("wave_id") or job.options.get("wave_id") or job.job_id
                    ),
                    status_code=200,
                    latency_ms=0,
                    supportability_status="complete",
                    completeness_status="complete",
                    failure_category="none",
                    failure_message=None,
                    captured_at=_utc_now(),
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                )
            ],
        )

    def _resume_existing_capture(
        self,
        *,
        job: ReportJobLedgerRecord,
        started_at: float,
    ) -> ReportJobLedgerRecord | None:
        try:
            snapshot = self._snapshot_store.get_snapshot_by_job(job.job_id)
        except ReportInputSnapshotNotFoundError:
            return None

        if snapshot.snapshot_payload.get("capture_status") == "failed":
            failure_category, failure_message, retry_eligible = _stored_capture_failure(
                snapshot.snapshot_payload
            )
            return self._mark_failed(
                job=job,
                started_at=started_at,
                failure_category=failure_category,
                failure_message=failure_message,
                retry_eligible=retry_eligible,
            )

        calls = self._snapshot_store.list_upstream_calls(snapshot.snapshot_id)
        integrity_error = _capture_integrity_error(snapshot, calls)
        if integrity_error is None:
            return self._mark_data_ready(job=job, started_at=started_at)
        if not calls:
            return None
        return self._mark_lineage_integrity_failed(job=job, started_at=started_at)

    def _complete_capture(
        self,
        *,
        job: ReportJobLedgerRecord,
        started_at: float,
        snapshot_request: ReportInputSnapshotCreateRequest,
        upstream_calls: list[ReportUpstreamCallCreateRequest],
        failure_category: str | None = None,
        failure_message: str | None = None,
        retry_eligible: bool = False,
    ) -> ReportJobLedgerRecord:
        snapshot_request = _bind_revision_identity(
            job=job,
            snapshot_request=snapshot_request,
            upstream_calls=upstream_calls,
        )
        try:
            snapshot, calls = self._snapshot_store.create_capture(
                snapshot=snapshot_request,
                upstream_calls=upstream_calls,
            )
        except (
            ReportInputSnapshotAlreadyCapturedError,
            ReportInputSnapshotLineageConflictError,
        ):
            return self._mark_lineage_integrity_failed(job=job, started_at=started_at)

        if failure_message:
            return self._mark_failed(
                job=job,
                started_at=started_at,
                failure_category=failure_category or "upstream_data_failed",
                failure_message=failure_message,
                retry_eligible=retry_eligible,
            )
        if _capture_integrity_error(snapshot, calls) is not None:
            return self._mark_lineage_integrity_failed(job=job, started_at=started_at)
        return self._mark_data_ready(job=job, started_at=started_at)

    def _mark_data_ready(
        self,
        *,
        job: ReportJobLedgerRecord,
        started_at: float,
    ) -> ReportJobLedgerRecord:
        record = self._job_ledger.mark_data_ready(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
        record_report_operation(
            operation="snapshot_capture",
            status=record.status,
            duration_seconds=perf_counter() - started_at,
        )
        return record

    def _mark_lineage_integrity_failed(
        self,
        *,
        job: ReportJobLedgerRecord,
        started_at: float,
    ) -> ReportJobLedgerRecord:
        return self._mark_failed(
            job=job,
            started_at=started_at,
            failure_category="data_incomplete",
            failure_message="Report input capture lineage is incomplete or inconsistent.",
            retry_eligible=False,
        )

    def _mark_failed(
        self,
        *,
        job: ReportJobLedgerRecord,
        started_at: float,
        failure_category: str,
        failure_message: str,
        retry_eligible: bool,
    ) -> ReportJobLedgerRecord:
        record = self._job_ledger.mark_failed(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            failure_category=failure_category,
            failure_message=failure_message,
            retry_eligible=retry_eligible,
        )
        record_report_operation(
            operation="snapshot_capture",
            status=record.status,
            failure_category=record.failure_category,
            duration_seconds=perf_counter() - started_at,
        )
        return record


def _accepted_snapshot_contract_version(job: ReportJobLedgerRecord) -> str:
    """The input-snapshot contract the job was ACCEPTED under (report#283
    finding 6) - never today's setting for a job that persisted its own.
    A legacy job without a persisted contract keeps the current setting,
    with no accepted-contract claim implied."""

    contract = job.accepted_document_contract or {}
    accepted = contract.get("input_snapshot_contract_version")
    if isinstance(accepted, str) and accepted.strip():
        return accepted
    family_stated = resolve_report_family(job.report_type).input_snapshot_contract_version
    return family_stated or str(settings.contract_version)


def _bind_revision_identity(
    *,
    job: ReportJobLedgerRecord,
    snapshot_request: ReportInputSnapshotCreateRequest,
    upstream_calls: list[ReportUpstreamCallCreateRequest],
) -> ReportInputSnapshotCreateRequest:
    """Mint the canonical revision identity for a successful capture and
    bind it BESIDE the payload (report#283): the payload bytes and their
    integrity hash stay untouched, so the id is never its own preimage. A
    failed capture mints nothing."""

    capture_failed = snapshot_request.snapshot_payload.get("capture_status") == "failed"
    snapshot_request = snapshot_request.model_copy(
        update={"lifecycle": snapshot_lifecycle_claim(capture_failed=capture_failed)}
    )
    minted = revision_for_capture(
        job=job,
        snapshot_payload=snapshot_request.snapshot_payload,
        upstream_services=tuple(call.service_name for call in upstream_calls),
    )
    if minted is None:
        return snapshot_request
    identity, vector = minted
    coherence = evaluate_source_cut_coherence(
        source_revisions=vector,
        business_as_of_date=job.as_of_date.isoformat(),
    )
    bound: ReportInputSnapshotCreateRequest = snapshot_request.model_copy(
        update={
            "report_revision_id": identity.report_revision_id,
            "series_digest": identity.series_digest,
            "source_revision_digest": identity.source_revision_digest,
            "factual_content_digest": identity.factual_content_digest,
            "factual_boundary_version": identity.factual_boundary_version,
            "source_revision_vector": vector.canonical(),
            "source_cut_coherence": coherence.model_dump(),
        }
    )
    return bound


def _first_portfolio_id(job: ReportJobLedgerRecord) -> str:
    portfolio_ids = job.portfolio_scope.get("portfolio_ids", [])
    if not portfolio_ids:
        raise ReportingValidationError("portfolio_scope_portfolio_ids_required")
    return str(portfolio_ids[0])


def _request_payload(job: ReportJobLedgerRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "as_of_date": job.as_of_date.isoformat(),
        **dict(job.options),
    }
    payload.pop("proposal_narrative_package", None)
    payload.pop("proposal_memo_package", None)
    if job.reporting_currency:
        payload["reporting_currency"] = job.reporting_currency
    return payload


def _proposal_narrative_package(job: ReportJobLedgerRecord) -> dict[str, Any] | None:
    package = job.options.get("proposal_narrative_package")
    return package if isinstance(package, dict) else None


def _proposal_memo_package(job: ReportJobLedgerRecord) -> dict[str, Any] | None:
    package = job.options.get("proposal_memo_package")
    return package if isinstance(package, dict) else None


def _portfolio_memory_lineage_summary(report_input: dict[str, Any]) -> dict[str, Any]:
    context = report_input.get("portfolio_memory_context")
    if not isinstance(context, dict):
        return {
            "portfolio_memory_status": "not_supplied",
        }

    raw_event_refs = context.get("event_refs")
    if not isinstance(raw_event_refs, list):
        raw_event_refs = []
    event_refs = [item for item in raw_event_refs if isinstance(item, dict)]
    return {
        "portfolio_memory_status": "supplied",
        "portfolio_memory_content_hash": context.get("content_hash"),
        "portfolio_memory_context_content_hash": context.get("context_content_hash"),
        "portfolio_memory_event_count": context.get("event_count", len(event_refs)),
        "portfolio_memory_supportability_state": context.get("supportability_state"),
        "portfolio_memory_support_boundary": context.get("support_boundary"),
        "portfolio_memory_event_ref_limit": _optional_int(context.get("event_ref_limit")),
        "portfolio_memory_event_ref_selection_policy": context.get("event_ref_selection_policy"),
        "portfolio_memory_event_refs_returned": _optional_int(
            context.get("event_refs_returned"),
        ),
        "portfolio_memory_event_refs_omitted": _optional_int(
            context.get("event_refs_omitted"),
        ),
        "portfolio_memory_event_refs_truncated": _optional_bool(
            context.get("event_refs_truncated"),
        ),
        "portfolio_memory_event_ref_count": len(event_refs),
    }


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _report_period_label(snapshot_payload: dict[str, Any]) -> str | None:
    """The report's own review period, for comparison against the brief's."""

    review_period = snapshot_payload.get("reviewPeriod")
    if not isinstance(review_period, dict):
        return None
    return _optional_str(review_period.get("label"))


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None


def _required_sha256(report_input: dict[str, Any], field_name: str, owner: str) -> str:
    value = _optional_str(report_input.get(field_name))
    if not value or not value.startswith("sha256:"):
        raise ValueError(f"{owner}.{field_name} must use sha256 lineage")
    return value


def _proof_pack_source_system(source_system: str | None, source_type: str) -> str:
    if source_system == "lotus-idea" and source_type == "LOTUS_IDEA_EVIDENCE_PACK_REPORT_INPUT":
        return "lotus-idea"
    return "lotus-manage"


def _proof_pack_source_endpoint(source_system: str) -> str:
    if source_system == "lotus-idea":
        return "/reports/idea-evidence-packs/materializations"
    return "/api/v1/rebalance/proof-packs/{proof_pack_id}/report-input"


def _proof_pack_source_method(source_system: str) -> str:
    if source_system == "lotus-idea":
        return "POST"
    return "GET"


def _proof_pack_source_contract_version(source_system: str) -> str:
    if source_system == "lotus-idea":
        return "LotusIdeaEvidencePackReportInput.1.0"
    return "DpmProofPackReportInput.1.0"


def _overall_posture(calls: list[_RecordedUpstreamCall]) -> SnapshotPosture:
    if not calls:
        return "error"
    values = {call.supportability_status for call in calls}
    if "error" in values:
        return "error"
    if "unavailable" in values:
        return "unavailable"
    if "not_supported" in values:
        return "not_supported"
    if "redacted" in values:
        return "redacted"
    if "partial" in values:
        return "partial"
    return "complete"


def _lineage_summary(
    calls: list[_RecordedUpstreamCall],
    *,
    proposal_narrative_package: dict[str, Any] | None = None,
    proposal_memo_package: dict[str, Any] | None = None,
    advisor_commentary_package: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = {
        "source_services": sorted({call.service_name for call in calls}),
        "call_count": len(calls),
        "supportability_status": _overall_posture(calls),
        "partial_call_count": sum(1 for call in calls if call.supportability_status == "partial"),
        "unavailable_call_count": sum(
            1 for call in calls if call.supportability_status == "unavailable"
        ),
        "not_supported_call_count": sum(
            1 for call in calls if call.supportability_status == "not_supported"
        ),
        "redacted_call_count": sum(1 for call in calls if call.supportability_status == "redacted"),
    }
    if proposal_narrative_package is not None:
        summary_source_services = summary.get("source_services")
        source_services = set(
            summary_source_services if isinstance(summary_source_services, list) else []
        )
        source_services.add("lotus-advise")
        source_lineage = proposal_narrative_package.get("source_lineage")
        if not isinstance(source_lineage, dict):
            source_lineage = {}
        review = proposal_narrative_package.get("review")
        if not isinstance(review, dict):
            review = {}
        summary.update(
            {
                "source_services": sorted(source_services),
                "proposal_narrative_package_status": proposal_narrative_package.get(
                    "package_status"
                ),
                "proposal_narrative_review_state": review.get("review_state"),
                "proposal_narrative_source_hash": source_lineage.get("source_narrative_hash"),
            }
        )
    if proposal_memo_package is not None:
        summary_source_services = summary.get("source_services")
        source_services = set(
            summary_source_services if isinstance(summary_source_services, list) else []
        )
        source_services.add("lotus-advise")
        memo_review = proposal_memo_package.get("review")
        if not isinstance(memo_review, dict):
            memo_review = {}
        summary.update(
            {
                "source_services": sorted(source_services),
                "proposal_memo_package_status": proposal_memo_package.get("package_status"),
                "proposal_memo_id": proposal_memo_package.get("memo_id"),
                "proposal_memo_hash": proposal_memo_package.get("memo_hash"),
                "proposal_memo_review_action": memo_review.get("review_action"),
                "proposal_memo_reviewed_by": memo_review.get("reviewed_by"),
                "proposal_memo_reviewed_at": memo_review.get("reviewed_at"),
            }
        )
    if isinstance(advisor_commentary_package, dict):
        summary["advisor_commentary_status"] = advisor_commentary_package.get("status")
        if advisor_commentary_package.get("status") == "included":
            commentary_review = advisor_commentary_package.get("review")
            commentary_review = commentary_review if isinstance(commentary_review, dict) else {}
            summary.update(
                {
                    "advisor_brief_run_id": advisor_commentary_package.get("run_id"),
                    "advisor_brief_request_id": advisor_commentary_package.get("request_id"),
                    "advisor_brief_reviewed_by": commentary_review.get("reviewed_by"),
                    "advisor_brief_reviewed_at": commentary_review.get("reviewed_at"),
                    "advisor_brief_content_hash": advisor_commentary_package.get("content_hash"),
                }
            )
        else:
            summary["advisor_commentary_reason_code"] = advisor_commentary_package.get(
                "reason_code"
            )
    return summary


def _capture_integrity_error(
    snapshot: ReportInputSnapshotRecord,
    calls: list[ReportUpstreamCallRecord],
) -> str | None:
    summary = snapshot.lineage_summary
    declared_call_count = summary.get("call_count")
    if isinstance(declared_call_count, bool) or not isinstance(declared_call_count, int):
        return "lineage_call_count_invalid"
    if declared_call_count < 1:
        return "lineage_call_count_required"
    if declared_call_count != len(calls):
        return "lineage_call_count_mismatch"

    declared_services_value = summary.get("source_services")
    if not isinstance(declared_services_value, list):
        return "lineage_source_services_invalid"
    declared_services = {
        service.strip()
        for service in declared_services_value
        if isinstance(service, str) and service.strip()
    }
    actual_services = {call.service_name for call in calls}
    if not actual_services or not actual_services.issubset(declared_services):
        return "lineage_source_services_mismatch"
    if any(call.correlation_id != snapshot.correlation_id for call in calls):
        return "lineage_correlation_id_mismatch"
    if any(call.trace_id != snapshot.trace_id for call in calls):
        return "lineage_trace_id_mismatch"
    return None


def _map_job_failure(exc: Exception) -> tuple[str, str, bool]:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout", "Upstream report-data capture timed out.", True
    if isinstance(exc, ReportingUpstreamError):
        return "upstream_data_failed", "Upstream report-data capture failed.", True
    if isinstance(exc, (ReportingValidationError, ReportingNotFoundError)):
        return "validation_failed", "Requested report inputs were not fully supported.", False
    return "upstream_data_failed", "Upstream report-data capture failed.", True


def _stored_capture_failure(snapshot_payload: dict[str, Any]) -> tuple[str, str, bool]:
    category = snapshot_payload.get("failure_category")
    if category not in {"validation_failed", "upstream_data_failed", "timeout"}:
        category = "upstream_data_failed"
    message = snapshot_payload.get("failure_message")
    if not isinstance(message, str) or not message.strip():
        message = "Upstream report-data capture failed."
    return category, message, category in {"upstream_data_failed", "timeout"}
