from fastapi.testclient import TestClient

from app.main import app
from app.reporting_jobs.ledger import (
    MissingIdempotencyKeyError,
    ReportJobLedger,
    ReportJobNotFoundError,
)
from app.reporting_jobs.service import get_report_job_ledger


def _client(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    app.dependency_overrides[get_report_job_ledger] = lambda: ledger
    return TestClient(app), ledger


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
            "benchmark_code": "BMK_GLOBAL_BALANCED_60_40",
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


def test_portfolio_review_job_submit_status_and_cancel(tmp_path):
    client, ledger = _client(tmp_path)
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
        assert handle["status"] == "accepted"
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
        assert status_body["status"] == "accepted"
        assert status_body["current_step"] == "accepted"
        assert status_body["retry_eligible"] is False
        assert status_body["correlation_id"] == "corr-report-job-1"
        assert "sqlite" not in str(status_body).lower()

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
        assert event_statuses == ["accepted", "cancelled"]

        events_response = client.get(
            f"/reports/jobs/{handle['report_job_id']}/events",
            headers=_headers(),
        )
        assert events_response.status_code == 200
        events_body = events_response.json()
        assert events_body["report_job_id"] == handle["report_job_id"]
        assert [event["to_status"] for event in events_body["events"]] == [
            "accepted",
            "cancelled",
        ]
    finally:
        _clear_overrides()


def test_portfolio_review_job_submit_is_idempotent(tmp_path):
    client, _ledger = _client(tmp_path)
    try:
        first = client.post("/reports/portfolio-reviews", json=_payload(), headers=_headers())
        second = client.post("/reports/portfolio-reviews", json=_payload(), headers=_headers())

        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json() == first.json()
    finally:
        _clear_overrides()


def test_portfolio_review_job_rejects_missing_idempotency_key(tmp_path):
    client, _ledger = _client(tmp_path)
    try:
        response = client.post("/reports/portfolio-reviews", json=_payload(), headers={})

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "missing_idempotency_key"
    finally:
        _clear_overrides()


def test_portfolio_review_job_translates_ledger_missing_idempotency_error(tmp_path):
    client, ledger = _client(tmp_path)

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
    client, _ledger = _client(tmp_path)
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
    client, _ledger = _client(tmp_path)
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
    client, _ledger = _client(tmp_path)
    try:
        unknown = client.get("/reports/jobs/rjob_missing", headers=_headers())
        assert unknown.status_code == 404
        assert unknown.json()["detail"]["code"] == "report_job_not_found"

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


def test_report_job_events_and_cancel_translate_unknown_job(tmp_path):
    client, ledger = _client(tmp_path)

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
    events_get = schema["paths"]["/reports/jobs/{job_id}/events"]["get"]
    events_example = events_get["responses"]["200"]["content"]["application/json"]["example"]

    assert request_example["portfolio_scope"]["portfolio_ids"] == ["PB_SG_GLOBAL_BAL_001"]
    assert response_example["report_job_id"].startswith("rjob_")
    assert status_example["status"] == "accepted"
    assert events_example["events"][0]["event_type"] == "job_accepted"
    assert "RFC-" not in str(request_example)
    assert "RFC-" not in str(response_example)
    assert "RFC-" not in str(status_example)
    assert "RFC-" not in str(events_example)
    for schema_name in [
        "ReportJobHandleResponse",
        "ReportJobStatusResponse",
        "ReportJobStatusEventsResponse",
        "ReportStatusEvent",
    ]:
        properties = schema["components"]["schemas"][schema_name]["properties"]
        for property_contract in properties.values():
            assert property_contract.get("description")
