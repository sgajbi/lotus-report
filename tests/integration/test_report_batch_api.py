from fastapi.testclient import TestClient

from app.main import app
from app.report_batch_orchestrator.execution import BatchItemExecutionResult
from app.report_batch_orchestrator.ledger import (
    MissingBatchIdempotencyKeyError,
    ReportBatchLedger,
)
from app.report_batch_orchestrator.service import (
    get_report_batch_ledger,
    get_report_batch_worker,
)
from app.report_batch_orchestrator.worker import BatchWorkerRunResult


def _client(tmp_path):
    ledger = ReportBatchLedger(tmp_path / "batches.sqlite3")
    app.dependency_overrides[get_report_batch_ledger] = lambda: ledger
    return TestClient(app), ledger


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def _headers(idempotency_key: str = "batch-portfolio-review-2026-04-22") -> dict[str, str]:
    return {
        "Idempotency-Key": idempotency_key,
        "X-Actor-Id": "advisor-123",
        "X-Caller-Application": "lotus-gateway",
        "X-Tenant-Id": "tenant-sg",
        "X-Region": "APAC",
        "X-Booking-Center-Code": "SG",
        "X-Role": "advisor",
        "X-Correlation-ID": "corr-batch-1",
        "X-Trace-ID": "trace-batch-1",
    }


def _payload() -> dict[str, object]:
    return {
        "selector_mode": "explicit_portfolio_list",
        "portfolio_ids": ["PB_SG_GLOBAL_BAL_001", "PB_SG_GLOBAL_BAL_002"],
        "source_candidates": [
            {
                "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                "tenant_id": "tenant-sg",
                "region": "APAC",
                "active": True,
                "selected": True,
                "source_system": "lotus-core",
                "source_object": "PortfolioScope",
            },
            {
                "portfolio_id": "PB_SG_GLOBAL_BAL_002",
                "tenant_id": "tenant-sg",
                "region": "APAC",
                "active": True,
                "selected": True,
                "source_system": "lotus-core",
                "source_object": "PortfolioScope",
            },
        ],
        "as_of_date": "2026-04-22",
        "requested_output_formats": ["pdf"],
        "reporting_currency": "USD",
        "options": {"sections": ["OVERVIEW", "PERFORMANCE"]},
        "max_batch_size": 250,
    }


class _WorkerRunSuccess:
    async def run_once(self, **kwargs):
        return BatchWorkerRunResult(
            batch_id=kwargs["batch_id"],
            batch_status_before="materialized",
            batch_status_after="completed",
            recovered_count=0,
            leased_count=1,
            dispatched_count=1,
            executed_count=1,
            report_job_ids=["rjob_batch_run_once"],
            back_pressure_reasons=[],
            execution_results=[
                BatchItemExecutionResult(
                    batch_id=kwargs["batch_id"],
                    batch_item_id="rbci_batch_run_once",
                    report_job_id="rjob_batch_run_once",
                    item_status="succeeded",
                    report_job_status="archived",
                )
            ],
        )


class _WorkerRunPaused:
    async def run_once(self, **kwargs):
        return BatchWorkerRunResult(
            batch_id=kwargs["batch_id"],
            batch_status_before="paused",
            batch_status_after="paused",
            recovered_count=0,
            leased_count=0,
            dispatched_count=0,
            executed_count=0,
            skipped_reason="batch_not_runnable:paused",
        )


def test_report_batch_create_status_and_control_endpoints(tmp_path):
    client, _ledger = _client(tmp_path)
    try:
        create_response = client.post("/reports/batches", json=_payload(), headers=_headers())
        assert create_response.status_code == 202
        handle = create_response.json()
        batch_id = handle["batch_id"]

        status_response = client.get(f"/reports/batches/{batch_id}", headers=_headers())
        assert status_response.status_code == 200
        status_body = status_response.json()
        assert status_body["status"] == "materialized"
        assert status_body["status_counts"] == {"materialized": 2}
        assert [item["portfolio_id"] for item in status_body["items"]] == [
            "PB_SG_GLOBAL_BAL_001",
            "PB_SG_GLOBAL_BAL_002",
        ]

        pause_response = client.post(f"/reports/batches/{batch_id}:pause", headers=_headers())
        resume_response = client.post(f"/reports/batches/{batch_id}:resume", headers=_headers())
        retry_response = client.post(
            f"/reports/batches/{batch_id}:retry-failed", headers=_headers()
        )
        recover_response = client.post(
            f"/reports/batches/{batch_id}:recover-expired-leases",
            headers=_headers(),
        )
        cancel_response = client.post(f"/reports/batches/{batch_id}:cancel", headers=_headers())
        cancelled_status = client.get(f"/reports/batches/{batch_id}", headers=_headers()).json()

        assert pause_response.status_code == 200
        assert pause_response.json()["status"] == "paused"
        assert resume_response.status_code == 200
        assert resume_response.json()["status"] == "materialized"
        assert retry_response.status_code == 200
        assert retry_response.json()["affected_count"] == 0
        assert recover_response.status_code == 200
        assert recover_response.json()["recovered_count"] == 0
        assert cancel_response.status_code == 200
        assert cancel_response.json()["affected_count"] == 2
        assert cancelled_status["status"] == "cancelled"
        assert cancelled_status["status_counts"] == {"cancelled": 2}
    finally:
        _clear_overrides()


def test_report_batch_run_once_endpoint_returns_operator_safe_result(tmp_path):
    client, _ledger = _client(tmp_path)
    app.dependency_overrides[get_report_batch_worker] = lambda: _WorkerRunSuccess()
    try:
        create_response = client.post("/reports/batches", json=_payload(), headers=_headers())
        batch_id = create_response.json()["batch_id"]

        response = client.post(
            f"/reports/batches/{batch_id}:run-once",
            json={
                "worker_id": "lotus-report-batch-worker-unit",
                "recover_expired_leases": True,
                "runtime_load": {
                    "active_batches": 0,
                    "active_items": 0,
                    "active_upstream_jobs": 0,
                    "active_render_jobs": 0,
                    "active_archive_jobs": 0,
                },
            },
            headers=_headers(),
        )

        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "completed"
        assert body["batch_status_before"] == "materialized"
        assert body["batch_status_after"] == "completed"
        assert body["leased_count"] == 1
        assert body["dispatched_count"] == 1
        assert body["executed_count"] == 1
        assert body["report_job_ids"] == ["rjob_batch_run_once"]
        assert body["execution_results"] == [
            {
                "batch_item_id": "rbci_batch_run_once",
                "report_job_id": "rjob_batch_run_once",
                "item_status": "succeeded",
                "report_job_status": "archived",
                "failure_category": None,
                "retry_eligible": False,
            }
        ]
        assert body["status_url"] == f"/reports/batches/{batch_id}"
    finally:
        _clear_overrides()


def test_report_batch_run_once_endpoint_reports_non_runnable_batch(tmp_path):
    client, _ledger = _client(tmp_path)
    app.dependency_overrides[get_report_batch_worker] = lambda: _WorkerRunPaused()
    try:
        create_response = client.post("/reports/batches", json=_payload(), headers=_headers())
        batch_id = create_response.json()["batch_id"]

        response = client.post(
            f"/reports/batches/{batch_id}:run-once",
            json={"worker_id": "lotus-report-batch-worker-unit"},
            headers=_headers(),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "paused"
        assert response.json()["skipped_reason"] == "batch_not_runnable:paused"
        assert response.json()["dispatched_count"] == 0
        assert response.json()["executed_count"] == 0
    finally:
        _clear_overrides()


def test_report_batch_create_is_idempotent_and_rejects_conflicting_request(tmp_path):
    client, _ledger = _client(tmp_path)
    try:
        first = client.post("/reports/batches", json=_payload(), headers=_headers())
        second = client.post("/reports/batches", json=_payload(), headers=_headers())
        changed_payload = _payload()
        changed_payload["reporting_currency"] = "EUR"
        conflict = client.post("/reports/batches", json=changed_payload, headers=_headers())

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["batch_id"] == second.json()["batch_id"]
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    finally:
        _clear_overrides()


def test_report_batch_create_rejects_missing_context_and_invalid_selector(tmp_path):
    client, _ledger = _client(tmp_path)
    try:
        missing_context = client.post(
            "/reports/batches",
            json=_payload(),
            headers={"Idempotency-Key": "batch-missing-context"},
        )
        invalid_payload = _payload()
        invalid_payload["portfolio_ids"] = ["PB_SG_GLOBAL_BAL_999"]
        invalid_selector = client.post(
            "/reports/batches",
            json=invalid_payload,
            headers=_headers("batch-invalid-selector"),
        )

        assert missing_context.status_code == 400
        assert missing_context.json()["detail"]["code"] == "missing_caller_context"
        assert invalid_selector.status_code == 400
        assert invalid_selector.json()["detail"]["code"] == "portfolio_not_found"
    finally:
        _clear_overrides()


def test_report_batch_create_rejects_missing_idempotency_key(tmp_path):
    client, _ledger = _client(tmp_path)
    headers = _headers()
    headers.pop("Idempotency-Key")
    try:
        response = client.post("/reports/batches", json=_payload(), headers=headers)

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "missing_idempotency_key"
    finally:
        _clear_overrides()


def test_report_batch_create_maps_ledger_missing_idempotency_error(tmp_path):
    class MissingIdempotencyLedger:
        def create_batch(self, **_kwargs):
            raise MissingBatchIdempotencyKeyError

    client = TestClient(app)
    app.dependency_overrides[get_report_batch_ledger] = lambda: MissingIdempotencyLedger()
    try:
        response = client.post("/reports/batches", json=_payload(), headers=_headers())

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "missing_idempotency_key"
    finally:
        _clear_overrides()


def test_report_batch_status_and_control_return_not_found(tmp_path):
    client, _ledger = _client(tmp_path)
    try:
        status_response = client.get("/reports/batches/rbch_missing", headers=_headers())
        pause_response = client.post("/reports/batches/rbch_missing:pause", headers=_headers())

        assert status_response.status_code == 404
        assert status_response.json()["detail"]["code"] == "report_batch_not_found"
        assert pause_response.status_code == 404
        assert pause_response.json()["detail"]["code"] == "report_batch_not_found"
    finally:
        _clear_overrides()


def test_report_batch_run_once_maps_worker_failures():
    class MissingBatchWorker:
        async def run_once(self, **_kwargs):
            raise ValueError("report_batch_not_found")

    class InconsistentBatchWorker:
        async def run_once(self, **_kwargs):
            raise RuntimeError("batch_item_missing_lease_token")

    client = TestClient(app)
    try:
        app.dependency_overrides[get_report_batch_worker] = lambda: MissingBatchWorker()
        missing = client.post(
            "/reports/batches/rbch_missing:run-once",
            json={"worker_id": "lotus-report-batch-worker-unit"},
            headers=_headers(),
        )

        app.dependency_overrides[get_report_batch_worker] = lambda: InconsistentBatchWorker()
        inconsistent = client.post(
            "/reports/batches/rbch_inconsistent:run-once",
            json={"worker_id": "lotus-report-batch-worker-unit"},
            headers=_headers(),
        )

        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "report_batch_not_found"
        assert inconsistent.status_code == 409
        assert inconsistent.json()["detail"]["code"] == "batch_worker_run_failed"
    finally:
        _clear_overrides()


def test_report_batch_status_and_controls_map_unexpected_ledger_errors():
    class UnexpectedLedger:
        def get_batch(self, _batch_id):
            raise ValueError("unexpected_batch_condition")

        def pause_batch(self, **_kwargs):
            raise ValueError("unexpected_batch_condition")

        def resume_batch(self, **_kwargs):
            raise ValueError("unexpected_batch_condition")

        def cancel_batch(self, **_kwargs):
            raise ValueError("unexpected_batch_condition")

        def retry_failed_items(self, **_kwargs):
            raise ValueError("unexpected_batch_condition")

        def recover_expired_leases(self, **_kwargs):
            raise ValueError("unexpected_batch_condition")

    client = TestClient(app)
    app.dependency_overrides[get_report_batch_ledger] = lambda: UnexpectedLedger()
    try:
        responses = [
            client.get("/reports/batches/rbch_problem", headers=_headers()),
            client.post("/reports/batches/rbch_problem:pause", headers=_headers()),
            client.post("/reports/batches/rbch_problem:resume", headers=_headers()),
            client.post("/reports/batches/rbch_problem:cancel", headers=_headers()),
            client.post("/reports/batches/rbch_problem:retry-failed", headers=_headers()),
            client.post(
                "/reports/batches/rbch_problem:recover-expired-leases",
                headers=_headers(),
            ),
        ]

        assert {response.status_code for response in responses} == {400}
        assert {response.json()["detail"]["code"] for response in responses} == {
            "batch_operation_failed"
        }
    finally:
        _clear_overrides()


def test_report_batch_status_and_control_require_caller_context(tmp_path):
    client, _ledger = _client(tmp_path)
    try:
        status_response = client.get("/reports/batches/rbch_missing")
        pause_response = client.post("/reports/batches/rbch_missing:pause")

        assert status_response.status_code == 400
        assert status_response.json()["detail"]["code"] == "missing_caller_context"
        assert pause_response.status_code == 400
        assert pause_response.json()["detail"]["code"] == "missing_caller_context"
    finally:
        _clear_overrides()


def test_report_batch_openapi_examples_are_complete_and_product_safe():
    client = TestClient(app)
    schema = client.get("/openapi.json").json()

    create_post = schema["paths"]["/reports/batches"]["post"]
    create_example = create_post["requestBody"]["content"]["application/json"]["example"]
    handle_example = create_post["responses"]["202"]["content"]["application/json"]["example"]
    status_get = schema["paths"]["/reports/batches/{batch_id}"]["get"]
    status_example = status_get["responses"]["200"]["content"]["application/json"]["example"]
    retry_post = schema["paths"]["/reports/batches/{batch_id}:retry-failed"]["post"]
    run_once_post = schema["paths"]["/reports/batches/{batch_id}:run-once"]["post"]
    run_once_request = run_once_post["requestBody"]["content"]["application/json"]["example"]
    run_once_response = run_once_post["responses"]["200"]["content"]["application/json"]["example"]

    assert create_example["selector_mode"] == "explicit_portfolio_list"
    assert handle_example["batch_id"].startswith("rbch_")
    assert status_example["status_counts"] == {"materialized": 2}
    assert run_once_request["worker_id"] == "lotus-report-batch-worker-1"
    assert run_once_response["executed_count"] == 2
    assert "Report Batches" in create_post["tags"]
    assert "Use this endpoint" in create_post["description"]
    assert "retryable failed batch items" in retry_post["description"]
    assert "single-batch operator action" in run_once_post["description"]
    assert "RFC-" not in str(create_example)
    assert "RFC-" not in str(handle_example)
    assert "RFC-" not in str(status_example)
    assert "RFC-" not in str(run_once_request)
    assert "RFC-" not in str(run_once_response)
    for schema_name in [
        "BatchHandleResponse",
        "BatchStatusResponse",
        "BatchItemStatusResponse",
        "BatchControlResponse",
        "BatchRecoveryResponse",
        "BatchWorkerRunRequest",
        "BatchWorkerRunResponse",
        "BatchWorkerItemExecutionResponse",
    ]:
        properties = schema["components"]["schemas"][schema_name]["properties"]
        for property_contract in properties.values():
            assert property_contract.get("description")


def test_report_batch_ledger_service_factory_uses_runtime_settings():
    get_report_batch_ledger.cache_clear()
    try:
        ledger = get_report_batch_ledger()

        assert ledger.__class__.__name__ == "PostgresReportBatchLedger"
    finally:
        get_report_batch_ledger.cache_clear()
