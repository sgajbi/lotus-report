from fastapi.testclient import TestClient

from app.main import app
from app.report_batch_orchestrator.ledger import (
    MissingBatchIdempotencyKeyError,
    ReportBatchLedger,
)
from app.report_batch_orchestrator.service import (
    get_report_batch_ledger,
)


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

    assert create_example["selector_mode"] == "explicit_portfolio_list"
    assert handle_example["batch_id"].startswith("rbch_")
    assert status_example["status_counts"] == {"materialized": 2}
    assert "Report Batches" in create_post["tags"]
    assert "Use this endpoint" in create_post["description"]
    assert "retryable failed batch items" in retry_post["description"]
    assert "RFC-" not in str(create_example)
    assert "RFC-" not in str(handle_example)
    assert "RFC-" not in str(status_example)
    for schema_name in [
        "BatchHandleResponse",
        "BatchStatusResponse",
        "BatchItemStatusResponse",
        "BatchControlResponse",
        "BatchRecoveryResponse",
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
