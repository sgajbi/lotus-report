from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import app
from app.reporting_jobs.ledger import (
    MissingIdempotencyKeyError,
    ReportJobLedger,
    ReportJobNotFoundError,
)
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.models import (
    ReportInputSnapshotCreateRequest,
    ReportUpstreamCallCreateRequest,
)
from app.reporting_lineage.service import get_portfolio_review_snapshot_capture_service
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render.rerender_service import (
    PortfolioReviewRerenderService,
    get_portfolio_review_rerender_service,
)
from app.reporting_render.service import get_portfolio_review_render_orchestration_service
from app.routers.report_jobs import get_report_lineage_store


def _client(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    lineage_store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    app.dependency_overrides[get_report_job_ledger] = lambda: ledger
    app.dependency_overrides[get_report_lineage_store] = lambda: lineage_store
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        _FakeCaptureService(ledger, lineage_store)
    )
    app.dependency_overrides[get_portfolio_review_render_orchestration_service] = lambda: (
        _FakeRenderService()
    )
    return TestClient(app), ledger, lineage_store


def _clear_overrides():
    app.dependency_overrides.clear()


def _payload():
    return {
        "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        "as_of_date": "2026-04-22",
        "requested_output_formats": ["json"],
        "reporting_currency": "USD",
        "options": {
            "sections": ["OVERVIEW", "PERFORMANCE"],
            "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
        },
    }


def _headers(idempotency_key="portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22"):
    return {
        "Idempotency-Key": idempotency_key,
        "X-Actor-Id": "advisor-123",
        "X-Caller-Application": "lotus-gateway",
        "X-Tenant-Id": "tenant-sg",
        "X-Region": "APAC",
        "X-Booking-Center-Code": "SG",
        "X-Role": "advisor",
        "X-Correlation-ID": "corr-report-job-1",
        "X-Trace-ID": "trace-report-job-1",
    }


class _FakeCaptureService:
    def __init__(self, ledger: ReportJobLedger, lineage_store: ReportInputSnapshotStore):
        self._ledger = ledger
        self._lineage_store = lineage_store
        self.calls = 0

    async def capture_for_job(self, job):
        self.calls += 1
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
                report_data_contract_version="v1",
                portfolio_scope=job.portfolio_scope,
                as_of_date=job.as_of_date,
                snapshot_payload={
                    "report_id": (
                        "portfolio-review:"
                        f"{job.portfolio_scope['portfolio_ids'][0]}:"
                        f"{job.as_of_date.isoformat()}"
                    ),
                    "portfolio_id": job.portfolio_scope["portfolio_ids"][0],
                    "as_of_date": job.as_of_date.isoformat(),
                },
                snapshot_storage_ref=None,
                supportability_status="complete",
                completeness_status="complete",
                lineage_summary={
                    "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
                    "call_count": 1,
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
                    request_hash="sha256:req",
                    response_hash="sha256:resp",
                    response_ref=None,
                    status_code=200,
                    latency_ms=184,
                    supportability_status="complete",
                    completeness_status="complete",
                    failure_category="none",
                    failure_message=None,
                    captured_at=datetime.now(UTC),
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                )
            ],
        )
        return self._ledger.mark_data_ready(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )


class _FakeRenderService:
    async def render_for_job(self, job):
        return job


class _RerenderRenderClient:
    def __init__(self, *, status_code=201, payload=None):
        self.status_code = status_code
        self.payload = payload
        self.payloads = []

    async def submit_render_package(self, payload, **kwargs):
        self.payloads.append(payload)
        if self.payload is not None:
            return self.status_code, self.payload
        return self.status_code, {
            "status": "rendered",
            "render_job_id": payload["render_job_id"],
            "artifact_sha256": "sha256:rerender-artifact",
            "bounded_determinism_fingerprint": "fingerprint-rerender",
            "runtime_engine": "typst",
            "runtime_engine_version": "0.14.2",
            "render_duration_ms": 731,
            "artifact_base64": "JVBERi0xLjQ=",
        }


class _RerenderArchiveClient:
    def __init__(self, *, status_code=201, payload=None):
        self.status_code = status_code
        self.payload = payload or {"document_id": "doc_report_job_pdf_correction"}
        self.payloads = []

    async def archive_document(self, payload, **kwargs):
        self.payloads.append(payload)
        return self.status_code, self.payload


def _install_rerender_service(ledger, lineage_store, render_client, archive_client):
    service = PortfolioReviewRerenderService(
        render_client=render_client,
        archive_client=archive_client,
        snapshot_store=lineage_store,
        ledger=ledger,
    )
    app.dependency_overrides[get_portfolio_review_rerender_service] = lambda: service
    return service


def _create_archived_pdf_job(client, ledger):
    payload = _payload()
    payload["requested_output_formats"] = ["pdf"]
    response = client.post("/reports/portfolio-reviews", json=payload, headers=_headers())
    assert response.status_code == 202
    job_id = response.json()["report_job_id"]
    ready = ledger.get_job(job_id)
    rendered = ledger.mark_completed(
        job_id=ready.job_id,
        actor=ready.triggered_by,
        correlation_id=ready.correlation_id,
        trace_id=ready.trace_id,
        render_job_id=f"rdr_{ready.job_id}_pdf",
        output_format="pdf",
        template_id="portfolio-review",
        template_version="v1",
        artifact_sha256="sha256:artifact",
        bounded_determinism_fingerprint="fingerprint",
        runtime_engine="typst",
        runtime_engine_version="0.14.2",
        render_duration_ms=812,
    )
    ledger.mark_archiving(
        job_id=rendered.job_id,
        actor=rendered.triggered_by,
        correlation_id=rendered.correlation_id,
        trace_id=rendered.trace_id,
        archive_request_id=f"arch_rdr_{ready.job_id}_pdf",
    )
    return ledger.mark_archived(
        job_id=rendered.job_id,
        actor=rendered.triggered_by,
        correlation_id=rendered.correlation_id,
        trace_id=rendered.trace_id,
        archive_request_id=f"arch_rdr_{ready.job_id}_pdf",
        archive_document_id="doc_report_job_pdf",
    )


def test_portfolio_review_job_submit_status_and_cancel(tmp_path):
    client, ledger, _lineage_store = _client(tmp_path)
    try:
        submit_response = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers(),
        )

        assert submit_response.status_code == 202
        handle = submit_response.json()
        assert handle["report_request_id"].startswith("rrq_")
        assert handle["report_job_id"].startswith("rjob_")
        assert handle["status"] == "data_ready"
        assert handle["status_url"] == f"/reports/jobs/{handle['report_job_id']}"
        assert handle["idempotency_key"] == _headers()["Idempotency-Key"]

        status_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}",
            headers=_headers(),
        )
        assert status_response.status_code == 200
        status_body = status_response.json()
        assert status_body["report_job_id"] == handle["report_job_id"]
        assert status_body["report_type"] == "portfolio_review"
        assert status_body["portfolio_scope"] == {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]}
        assert status_body["status"] == "data_ready"
        assert status_body["current_step"] == "data_ready"
        assert status_body["retry_eligible"] is False
        assert status_body["correlation_id"] == "corr-report-job-1"
        assert "sqlite" not in str(status_body).lower()

        list_response = client.get(
            "/reports/jobs",
            params={
                "tenantId": "tenant-sg",
                "region": "APAC",
                "status": "data_ready",
                "portfolioId": "PB_SG_GLOBAL_BAL_001",
                "asOfDate": "2026-04-22",
            },
            headers=_headers(),
        )
        assert list_response.status_code == 200
        list_body = list_response.json()
        assert list_body["count"] == 1
        assert list_body["applied_filters"]["tenant_id"] == "tenant-sg"
        assert list_body["items"][0]["report_job_id"] == handle["report_job_id"]
        assert list_body["items"][0]["idempotency_key"] == _headers()["Idempotency-Key"]

        cancel_response = client.post(
            f"/reports/jobs/{handle['report_job_id']}/cancel",
            headers={
                "X-Actor-Id": "advisor-123",
                "X-Caller-Application": "lotus-gateway",
                "X-Tenant-Id": "tenant-sg",
                "X-Region": "APAC",
                "X-Correlation-ID": "corr-cancel",
            },
        )
        assert cancel_response.status_code == 200
        cancel_body = cancel_response.json()
        assert cancel_body["status"] == "cancelled"
        assert cancel_body["failure_category"] == "cancelled"
        assert cancel_body["cancel_requested"] is True
        assert cancel_body["cancelled_at"] is not None
        event_statuses = [
            event.to_status for event in ledger.list_status_events(handle["report_job_id"])
        ]
        assert event_statuses == ["accepted", "collecting_data", "data_ready", "cancelled"]

        events_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/events",
            headers=_headers(),
        )
        assert events_response.status_code == 200
        events_body = events_response.json()
        assert events_body["report_job_id"] == handle["report_job_id"]
        assert [event["to_status"] for event in events_body["events"]] == [
            "accepted",
            "collecting_data",
            "data_ready",
            "cancelled",
        ]

        diagnostics_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/diagnostics",
            headers=_headers(),
        )
        assert diagnostics_response.status_code == 200
        diagnostics_body = diagnostics_response.json()
        assert diagnostics_body["report_job_id"] == handle["report_job_id"]
        assert diagnostics_body["status"]["status"] == "cancelled"
        assert diagnostics_body["event_count"] == 4
        assert diagnostics_body["latest_event"]["to_status"] == "cancelled"
        assert diagnostics_body["snapshot"]["snapshot_id"].startswith("rsnap_")
        assert diagnostics_body["lineage"]["upstream_call_count"] == 1
        assert diagnostics_body["lineage"]["source_services"] == ["lotus-core"]
        assert diagnostics_body["operation_links"]["status_url"] == (
            f"/reports/jobs/{handle['report_job_id']}"
        )
        assert "snapshot_payload" not in str(diagnostics_body).lower()
        assert "storage_ref" not in str(diagnostics_body).lower()
        assert "response_payload" not in str(diagnostics_body).lower()
    finally:
        _clear_overrides()


def test_report_job_list_requires_filter(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        response = client.get("/reports/jobs", headers=_headers())

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "invalid_report_job_filters"
    finally:
        _clear_overrides()


def test_portfolio_review_job_submit_is_idempotent(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        first = client.post("/reports/portfolio-reviews", json=_payload(), headers=_headers())
        second = client.post("/reports/portfolio-reviews", json=_payload(), headers=_headers())

        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json() == first.json()
    finally:
        _clear_overrides()


def test_portfolio_review_job_submit_can_complete_pdf_render_flow(tmp_path):
    client, ledger, _lineage_store = _client(tmp_path)
    try:
        payload = _payload()
        payload["requested_output_formats"] = ["pdf"]

        class _CompletingRenderService:
            async def render_for_job(self, job):
                rendered = ledger.mark_completed(
                    job_id=job.job_id,
                    actor=job.triggered_by,
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                    render_job_id=f"rdr_{job.job_id}_pdf",
                    output_format="pdf",
                    template_id="portfolio-review",
                    template_version="v1",
                    artifact_sha256="sha256:artifact",
                    bounded_determinism_fingerprint="fingerprint",
                    runtime_engine="typst",
                    runtime_engine_version="0.14.2",
                    render_duration_ms=812,
                )
                ledger.mark_archiving(
                    job_id=job.job_id,
                    actor=job.triggered_by,
                    correlation_id=job.correlation_id,
                    trace_id=job.trace_id,
                    archive_request_id=f"arch_rdr_{job.job_id}_pdf",
                )
                return ledger.mark_archived(
                    job_id=rendered.job_id,
                    actor=rendered.triggered_by,
                    correlation_id=rendered.correlation_id,
                    trace_id=rendered.trace_id,
                    archive_request_id=f"arch_rdr_{job.job_id}_pdf",
                    archive_document_id="doc_report_job_pdf",
                )

        app.dependency_overrides[get_portfolio_review_render_orchestration_service] = lambda: (
            _CompletingRenderService()
        )

        response = client.post("/reports/portfolio-reviews", json=payload, headers=_headers())

        assert response.status_code == 202
        handle = response.json()
        assert handle["status"] == "archived"

        status_response = client.get(f"/reports/jobs/{handle['report_job_id']}", headers=_headers())
        assert status_response.status_code == 200
        body = status_response.json()
        assert body["status"] == "archived"
        assert body["render"]["render_job_id"] == f"rdr_{handle['report_job_id']}_pdf"
        assert body["render"]["artifact_sha256"] == "sha256:artifact"
        assert body["archive"]["archive_request_id"] == f"arch_rdr_{handle['report_job_id']}_pdf"
        assert body["archive"]["document_id"] == "doc_report_job_pdf"
    finally:
        _clear_overrides()


def test_portfolio_review_job_does_not_recapture_collecting_data_replay(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    capture_service = _FakeCaptureService(ledger, lineage_store)
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        capture_service
    )

    original_create = ledger.create_portfolio_review_job

    def _return_collecting_data_on_replay(**kwargs):
        record = original_create(**kwargs)
        if capture_service.calls == 0:
            return record
        return record.model_copy(
            update={
                "status": "collecting_data",
                "current_step": "collecting_data",
            }
        )

    ledger.create_portfolio_review_job = _return_collecting_data_on_replay
    try:
        first = client.post("/reports/portfolio-reviews", json=_payload(), headers=_headers())
        second = client.post("/reports/portfolio-reviews", json=_payload(), headers=_headers())

        assert first.status_code == 202
        assert second.status_code == 202
        assert capture_service.calls == 1
        assert second.json()["report_job_id"] == first.json()["report_job_id"]
    finally:
        _clear_overrides()


def test_portfolio_review_job_rejects_missing_idempotency_key(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        response = client.post("/reports/portfolio-reviews", json=_payload(), headers={})

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "missing_idempotency_key"
    finally:
        _clear_overrides()


def test_portfolio_review_job_translates_ledger_missing_idempotency_error(tmp_path):
    client, ledger, _lineage_store = _client(tmp_path)

    def _raise_missing_key(**_kwargs):
        raise MissingIdempotencyKeyError("missing_idempotency_key")

    ledger.create_portfolio_review_job = _raise_missing_key
    try:
        response = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-ledger-missing-key"),
        )

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "missing_idempotency_key"
    finally:
        _clear_overrides()


def test_portfolio_review_job_rejects_missing_caller_context(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        headers = {"Idempotency-Key": "portfolio-review-missing-context"}
        response = client.post("/reports/portfolio-reviews", json=_payload(), headers=headers)

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "missing_caller_context"
        assert detail["missing_headers"] == [
            "X-Actor-Id",
            "X-Caller-Application",
            "X-Tenant-Id",
            "X-Region",
        ]
    finally:
        _clear_overrides()


def test_portfolio_review_job_rejects_idempotency_conflict(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        first = client.post("/reports/portfolio-reviews", json=_payload(), headers=_headers())
        changed_payload = _payload()
        changed_payload["reporting_currency"] = "CHF"
        conflict = client.post(
            "/reports/portfolio-reviews",
            json=changed_payload,
            headers=_headers(),
        )

        assert first.status_code == 202
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    finally:
        _clear_overrides()


def test_report_job_unknown_and_duplicate_cancel_are_product_safe(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        unknown = client.get("/reports/jobs/rjob_missing", headers=_headers())
        assert unknown.status_code == 404
        assert unknown.json()["detail"]["code"] == "report_job_not_found"
        unknown_diagnostics = client.get(
            "/reports/jobs/rjob_missing/diagnostics", headers=_headers()
        )
        assert unknown_diagnostics.status_code == 404
        assert unknown_diagnostics.json()["detail"]["code"] == "report_job_not_found"

        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-cancel-repeat"),
        ).json()
        first_cancel = client.post(
            f"/reports/jobs/{handle['report_job_id']}/cancel",
            headers=_headers("portfolio-review-cancel-repeat"),
        )
        duplicate_cancel = client.post(
            f"/reports/jobs/{handle['report_job_id']}/cancel",
            headers=_headers("portfolio-review-cancel-repeat"),
        )

        assert first_cancel.status_code == 200
        assert duplicate_cancel.status_code == 409
        assert duplicate_cancel.json()["detail"]["code"] == "report_job_cannot_be_cancelled"
        assert "traceback" not in str(duplicate_cancel.json()).lower()
    finally:
        _clear_overrides()


def test_report_job_diagnostics_requires_caller_context(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        response = client.get("/reports/jobs/rjob_missing/diagnostics")

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "missing_caller_context"
    finally:
        _clear_overrides()


def test_report_job_diagnostics_reports_missing_snapshot_without_payload_leak(tmp_path):
    client, _ledger, lineage_store = _client(tmp_path)

    class _NoSnapshotCaptureService:
        async def capture_for_job(self, job):
            return job

    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        _NoSnapshotCaptureService()
    )
    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-no-snapshot"),
        ).json()

        response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/diagnostics",
            headers=_headers("portfolio-review-no-snapshot"),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["snapshot"] is None
        assert body["lineage"] is None
        assert body["operation_links"]["snapshot_url"] is None
        assert body["operation_links"]["lineage_url"] is None
        assert body["diagnostic_flags"] == ["snapshot_not_captured"]
        assert "snapshot_payload" not in str(body).lower()
        assert lineage_store.list_upstream_calls("missing") == []
    finally:
        _clear_overrides()


def test_report_job_diagnostics_translates_lineage_store_unavailable(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)

    class _UnavailableLineageStore:
        def get_snapshot_by_job(self, _report_job_id):
            raise RuntimeError("database connection failed")

    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers("portfolio-review-lineage-unavailable"),
        ).json()
        app.dependency_overrides[get_report_lineage_store] = lambda: _UnavailableLineageStore()

        response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/diagnostics",
            headers=_headers("portfolio-review-lineage-unavailable"),
        )

        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "report_lineage_store_unavailable"
        assert "database" not in str(response.json()).lower()
    finally:
        _clear_overrides()


def test_report_job_events_and_cancel_translate_unknown_job(tmp_path):
    client, ledger, _lineage_store = _client(tmp_path)

    def _raise_not_found(*_args, **_kwargs):
        raise ReportJobNotFoundError("report_job_not_found")

    ledger.get_job = _raise_not_found
    ledger.cancel_job = _raise_not_found
    try:
        events = client.get("/reports/jobs/rjob_missing/events", headers=_headers())
        cancel = client.post("/reports/jobs/rjob_missing/cancel", headers=_headers())

        assert events.status_code == 404
        assert events.json()["detail"]["code"] == "report_job_not_found"
        assert cancel.status_code == 404
        assert cancel.json()["detail"]["code"] == "report_job_not_found"
    finally:
        _clear_overrides()


def test_report_job_openapi_examples_are_full_and_do_not_leak_rfc_names():
    client = TestClient(app)
    schema = client.get("/openapi.json").json()

    submit_post = schema["paths"]["/reports/portfolio-reviews"]["post"]
    request_example = submit_post["requestBody"]["content"]["application/json"]["example"]
    response_example = submit_post["responses"]["202"]["content"]["application/json"]["example"]
    status_get = schema["paths"]["/reports/jobs/{job_id}"]["get"]
    status_example = status_get["responses"]["200"]["content"]["application/json"]["example"]
    list_get = schema["paths"]["/reports/jobs"]["get"]
    list_example = list_get["responses"]["200"]["content"]["application/json"]["example"]
    events_get = schema["paths"]["/reports/jobs/{job_id}/events"]["get"]
    events_example = events_get["responses"]["200"]["content"]["application/json"]["example"]
    diagnostics_get = schema["paths"]["/reports/jobs/{job_id}/diagnostics"]["get"]
    diagnostics_example = diagnostics_get["responses"]["200"]["content"]["application/json"][
        "example"
    ]

    assert request_example["portfolio_scope"]["portfolio_ids"] == ["PB_SG_GLOBAL_BAL_001"]
    assert response_example["report_job_id"].startswith("rjob_")
    assert status_example["status"] == "archived"
    assert status_example["render"]["render_job_id"].startswith("rdr_")
    assert status_example["archive"]["document_id"].startswith("doc_")
    assert list_example["items"][0]["report_job_id"].startswith("rjob_")
    assert events_example["events"][0]["event_type"] == "job_accepted"
    assert diagnostics_example["lineage"]["upstream_call_count"] == 3
    assert diagnostics_example["operation_links"]["lineage_url"].endswith("/lineage")
    assert "snapshot_payload" not in str(diagnostics_example)
    assert "Report Jobs" in list_get["tags"]
    assert "Report Jobs" in diagnostics_get["tags"]
    assert "what" in list_get["description"].lower() or "returns" in list_get["description"].lower()
    assert (
        "when" in list_get["description"].lower()
        or "use this endpoint" in list_get["description"].lower()
    )
    assert "use this endpoint" in diagnostics_get["description"].lower()
    assert "RFC-" not in str(request_example)
    assert "RFC-" not in str(response_example)
    assert "RFC-" not in str(status_example)
    assert "RFC-" not in str(list_example)
    assert "RFC-" not in str(events_example)
    assert "RFC-" not in str(diagnostics_example)
    for schema_name in [
        "ReportJobHandleResponse",
        "ReportJobStatusResponse",
        "ReportJobDiagnosticsResponse",
        "ReportJobSnapshotDiagnostics",
        "ReportJobLineageDiagnostics",
        "ReportJobOperationLinks",
        "ReportJobListResponse",
        "ReportJobListItem",
        "ReportJobListFilters",
        "ReportJobStatusEventsResponse",
        "ReportStatusEvent",
        "ApiErrorResponse",
        "ApiErrorDetail",
    ]:
        properties = schema["components"]["schemas"][schema_name]["properties"]
        for property_contract in properties.values():
            assert property_contract.get("description")


def test_report_job_snapshot_and_lineage_endpoints_are_support_safe(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers(),
        ).json()

        snapshot_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/snapshot",
            headers=_headers(),
        )
        assert snapshot_response.status_code == 200
        snapshot_body = snapshot_response.json()
        assert snapshot_body["report_job_id"] == handle["report_job_id"]
        assert snapshot_body["supportability_status"] == "complete"

        lineage_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/lineage",
            headers=_headers(),
        )
        assert lineage_response.status_code == 200
        lineage_body = lineage_response.json()
        assert lineage_body["snapshot"]["report_job_id"] == handle["report_job_id"]
        assert lineage_body["upstream_calls"][0]["service_name"] == "lotus-core"
        assert "response_payload" not in str(lineage_body).lower()

        snapshot_id = snapshot_body["snapshot_id"]
        snapshot_by_id = client.get(f"/reports/snapshots/{snapshot_id}", headers=_headers())
        assert snapshot_by_id.status_code == 200
        assert snapshot_by_id.json()["snapshot_id"] == snapshot_id

        snapshot_lineage = client.get(
            f"/reports/snapshots/{snapshot_id}/lineage",
            headers=_headers(),
        )
        assert snapshot_lineage.status_code == 200
        assert snapshot_lineage.json()["snapshot"]["snapshot_id"] == snapshot_id
    finally:
        _clear_overrides()


def test_report_job_snapshot_endpoints_translate_missing_snapshot_rows(tmp_path):
    client, _ledger, _lineage_store = _client(tmp_path)
    try:
        missing_job_snapshot = client.get("/reports/jobs/rjob_missing/snapshot", headers=_headers())
        missing_job_lineage = client.get("/reports/jobs/rjob_missing/lineage", headers=_headers())
        missing_snapshot = client.get("/reports/snapshots/rsnap_missing", headers=_headers())
        missing_snapshot_lineage = client.get(
            "/reports/snapshots/rsnap_missing/lineage",
            headers=_headers(),
        )

        assert missing_job_snapshot.status_code == 404
        assert missing_job_snapshot.json()["detail"]["code"] == "report_job_not_found"
        assert missing_job_lineage.status_code == 404
        assert missing_job_lineage.json()["detail"]["code"] == "report_job_not_found"
        assert missing_snapshot.status_code == 404
        assert missing_snapshot.json()["detail"]["code"] == "report_snapshot_not_found"
        assert missing_snapshot_lineage.status_code == 404
        assert missing_snapshot_lineage.json()["detail"]["code"] == "report_snapshot_not_found"
    finally:
        _clear_overrides()


def test_report_job_rerender_uses_existing_snapshot_and_archives_correction(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    render_client = _RerenderRenderClient()
    archive_client = _RerenderArchiveClient()
    _install_rerender_service(ledger, lineage_store, render_client, archive_client)
    capture_service = _FakeCaptureService(ledger, lineage_store)
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: (
        capture_service
    )
    try:
        job = _create_archived_pdf_job(client, ledger)
        snapshot = lineage_store.get_snapshot_by_job(job.job_id)
        capture_calls_after_create = capture_service.calls

        response = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=_headers(f"rerender-{job.job_id}-template-correction"),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "archived"
        assert body["snapshot_id"] == snapshot.snapshot_id
        assert body["snapshot_hash"] == snapshot.snapshot_hash
        assert body["previous_render_job_id"] == f"rdr_{job.job_id}_pdf"
        assert body["previous_archive_document_id"] == "doc_report_job_pdf"
        assert body["render"]["render_job_id"].startswith("rdr_rrnd_")
        assert body["render"]["render_job_id"] != job.render_job_id
        assert body["archive"]["document_id"] == "doc_report_job_pdf_correction"
        assert body["archive_consequence"] == "correction"
        assert capture_service.calls == capture_calls_after_create

        assert len(render_client.payloads) == 1
        assert render_client.payloads[0]["snapshot_id"] == snapshot.snapshot_id
        assert render_client.payloads[0]["snapshot_hash"] == snapshot.snapshot_hash
        assert render_client.payloads[0]["render_attempt_id"] == body["rerender_attempt_id"]
        assert len(archive_client.payloads) == 1
        metadata = archive_client.payloads[0]["metadata"]
        assert metadata["snapshot_id"] == snapshot.snapshot_id
        assert metadata["snapshot_hash"] == snapshot.snapshot_hash
        assert metadata["render_attempt_id"] == body["rerender_attempt_id"]
        assert metadata["supersedes_render_job_id"] == f"rdr_{job.job_id}_pdf"
        assert metadata["supersedes_archive_document_id"] == "doc_report_job_pdf"
        assert metadata["archive_consequence"] == "correction"

        events = ledger.list_status_events(job.job_id)
        assert [event.event_type for event in events][-2:] == [
            "job_rerender_requested",
            "job_rerender_archived",
        ]
    finally:
        _clear_overrides()


def test_report_job_rerender_is_idempotent(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    render_client = _RerenderRenderClient()
    archive_client = _RerenderArchiveClient()
    _install_rerender_service(ledger, lineage_store, render_client, archive_client)
    try:
        job = _create_archived_pdf_job(client, ledger)
        headers = _headers(f"rerender-{job.job_id}-same-key")

        first = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=headers,
        )
        second = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=headers,
        )

        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json() == first.json()
        assert len(render_client.payloads) == 1
        assert len(archive_client.payloads) == 1
    finally:
        _clear_overrides()


def test_report_job_rerender_rejects_non_archived_job(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    _install_rerender_service(
        ledger,
        lineage_store,
        _RerenderRenderClient(),
        _RerenderArchiveClient(),
    )
    try:
        handle = client.post(
            "/reports/portfolio-reviews",
            json=_payload(),
            headers=_headers(),
        ).json()

        response = client.post(
            f"/reports/jobs/{handle['report_job_id']}/rerender",
            json={"reason": "Template correction."},
            headers=_headers(f"rerender-{handle['report_job_id']}-invalid"),
        )

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "report_job_cannot_be_rerendered"
    finally:
        _clear_overrides()


def test_report_job_rerender_reports_missing_snapshot(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    _install_rerender_service(
        ledger,
        lineage_store,
        _RerenderRenderClient(),
        _RerenderArchiveClient(),
    )
    try:
        job = _create_archived_pdf_job(client, ledger)
        missing_snapshot_store = ReportInputSnapshotStore(tmp_path / "empty-lineage.sqlite3")
        _install_rerender_service(
            ledger,
            missing_snapshot_store,
            _RerenderRenderClient(),
            _RerenderArchiveClient(),
        )

        response = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=_headers(f"rerender-{job.job_id}-missing-snapshot"),
        )

        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "report_job_not_found"
    finally:
        _clear_overrides()


def test_report_job_rerender_records_render_validation_failure(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    render_client = _RerenderRenderClient(
        status_code=422,
        payload={
            "detail": {
                "code": "render_package_invalid",
                "message": "Render package failed contract validation.",
            }
        },
    )
    archive_client = _RerenderArchiveClient()
    _install_rerender_service(ledger, lineage_store, render_client, archive_client)
    try:
        job = _create_archived_pdf_job(client, ledger)

        response = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=_headers(f"rerender-{job.job_id}-render-validation"),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "failed"
        assert body["failure_category"] == "render_validation_failed"
        assert body["retry_eligible"] is False
        assert body["archive"] is None
        assert len(archive_client.payloads) == 0
        assert ledger.list_status_events(job.job_id)[-1].event_type == "job_rerender_failed"
    finally:
        _clear_overrides()


def test_report_job_rerender_records_archive_failure(tmp_path):
    client, ledger, lineage_store = _client(tmp_path)
    render_client = _RerenderRenderClient()
    archive_client = _RerenderArchiveClient(
        status_code=503,
        payload={
            "detail": {
                "code": "archive_storage_unavailable",
                "message": "Archive storage is unavailable.",
            }
        },
    )
    _install_rerender_service(ledger, lineage_store, render_client, archive_client)
    try:
        job = _create_archived_pdf_job(client, ledger)

        response = client.post(
            f"/reports/jobs/{job.job_id}/rerender",
            json={"reason": "Template correction."},
            headers=_headers(f"rerender-{job.job_id}-archive-failure"),
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "failed"
        assert body["failure_category"] == "archive_storage_failed"
        assert body["retry_eligible"] is True
        assert body["archive"]["archive_request_id"].startswith("arch_rdr_rrnd_")
        assert ledger.list_status_events(job.job_id)[-1].event_type == "job_rerender_failed"
    finally:
        _clear_overrides()
