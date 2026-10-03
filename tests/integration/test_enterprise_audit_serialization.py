"""Actual registered HTTP, shipped JSON handler and durable admission audit truth."""

import io
import json
import logging
import os
import socket
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import httpx
import pytest
import uvicorn

from app.main import app
from app.observability import JsonFormatter, setup_logging
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import ReportJobListFilters
from app.reporting_jobs.service import get_report_job_ledger
from tests.integration.test_report_job_api import _headers, _payload


@pytest.fixture
def audit_http_world(tmp_path, monkeypatch):
    for name in (
        "ENTERPRISE_ENFORCE_AUTHZ",
        "ENTERPRISE_ENFORCE_READ_AUTHZ",
        "ENTERPRISE_AUDIT_READS",
    ):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("ENTERPRISE_RUNTIME_PROFILE", "local")
    monkeypatch.setenv("ENTERPRISE_POLICY_VERSION", "synthetic-audit-v1")
    monkeypatch.setenv(
        "ENTERPRISE_CAPABILITY_RULES_JSON",
        json.dumps(
            {
                "POST /reports/portfolio-reviews": "reports.submit",
                "GET /reports/jobs": "reports.read",
            }
        ),
    )
    ledger_path = tmp_path / "audit-jobs.sqlite3"
    ledger = ReportJobLedger(ledger_path)
    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_report_job_ledger] = lambda: ledger
    root = logging.getLogger()
    previous_handlers, previous_level = list(root.handlers), root.level
    setup_logging()
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler) and isinstance(
        handler.formatter, JsonFormatter
    )
    stream = io.StringIO()
    original_stream = handler.setStream(stream)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", log_config=None, lifespan="off"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]})
    thread.start()
    try:
        deadline = monotonic() + 10
        while not server.started and thread.is_alive() and monotonic() < deadline:
            sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            yield client, ledger, stream, ledger_path
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        handler.setStream(original_stream)
        handler.close()
        root.handlers = previous_handlers
        root.setLevel(previous_level)
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        assert not thread.is_alive()
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        if target := os.getenv("REPORT_AUDIT_EVIDENCE_DIR"):
            path = Path(target)
            path.mkdir(parents=True, exist_ok=True)
            (path / "listener-closed.json").write_text(
                json.dumps({"report_listener_closed": True}), encoding="utf-8"
            )


def test_actual_http_audit_reconciles_requests_and_persisted_admission(
    audit_http_world, monkeypatch
):
    client, ledger, stream, ledger_path = audit_http_world
    captures = []

    def call(name, method, path, headers, status, *, body=None, denial=None, audited=True):
        headers = dict(headers)
        if "X-Correlation-ID" in headers:
            headers["X-Correlation-ID"] = f"audit-{name}"
        before = len(stream.getvalue())
        response = client.request(method, path, headers=headers, json=body)
        assert response.status_code == status
        raw = stream.getvalue()[before:]
        records = [json.loads(line) for line in raw.splitlines()]
        events = [record for record in records if record.get("message") == "enterprise_audit_event"]
        assert len(events) == int(audited)
        if audited:
            event = events[0]
            audit = event["audit"]
            assert event["logger"] == "enterprise_readiness"
            assert audit["schema_version"] == "lotus-report.audit.v1"
            assert (
                audit["service"] == "lotus-report"
                and audit["policy_version"] == "synthetic-audit-v1"
            )
            assert audit["action"] == f"{'DENY ' if denial else ''}{method} {path}"
            for field, header in {
                "actor_id": "X-Actor-Id",
                "tenant_id": "X-Tenant-Id",
                "role": "X-Role",
                "correlation_id": "X-Correlation-ID",
            }.items():
                assert audit[field] == (headers.get(header) or None)
            assert datetime.fromisoformat(audit["timestamp_utc"]).utcoffset().total_seconds() == 0
            expected_metadata = {"status_code": status}
            if denial:
                expected_metadata["reason"] = denial
                assert response.json()["reason"] == denial
            elif method == "GET":
                expected_metadata["access_type"] = "read"
            assert audit["metadata"] == expected_metadata
            assert "audit_serialization_error" not in event
        assert "synthetic-service-secret" not in raw
        captures.append(
            {
                "name": name,
                "method": method,
                "path": path,
                "headers": {
                    key: value for key, value in headers.items() if key != "X-Service-Identity"
                },
                "status": status,
                "response": response.json(),
                "events": events,
                "serialized_output": raw,
                "service_identity_present": "X-Service-Identity" in headers,
            }
        )
        return response

    headers = _headers("audit-allowed") | {
        "X-Service-Identity": "synthetic-service-secret",
        "X-Capabilities": "reports.submit,reports.read",
    }
    payload = _payload()
    first = call("allowed", "POST", "/reports/portfolio-reviews", headers, 202, body=payload)
    job_id = first.json()["report_job_id"]
    replay = call("replay", "POST", "/reports/portfolio-reviews", headers, 202, body=payload)
    assert replay.json()["report_job_id"] == job_id
    conflict = deepcopy(payload)
    conflict["reporting_currency"] = "SGD"
    call("conflict", "POST", "/reports/portfolio-reviews", headers, 409, body=conflict)
    call("invalid-body", "POST", "/reports/portfolio-reviews", headers, 422, body={})
    call(
        "missing-idempotency",
        "POST",
        "/reports/portfolio-reviews",
        {key: value for key, value in headers.items() if key != "Idempotency-Key"},
        400,
        body=payload,
    )
    call(
        "missing-capability",
        "POST",
        "/reports/portfolio-reviews",
        headers | {"X-Capabilities": "reports.read"},
        403,
        body=payload,
        denial="missing_capability:reports.submit",
    )
    call(
        "missing-service",
        "POST",
        "/reports/portfolio-reviews",
        {key: value for key, value in headers.items() if key != "X-Service-Identity"},
        403,
        body=payload,
        denial="missing_service_identity",
    )
    call(
        "missing-identity",
        "POST",
        "/reports/portfolio-reviews",
        {},
        403,
        body=payload,
        denial="missing_headers:x-actor-id,x-correlation-id,x-role,x-tenant-id",
    )
    call("read", "GET", f"/reports/jobs/{job_id}", headers, 200)
    call(
        "foreign-read",
        "GET",
        f"/reports/jobs/{job_id}",
        headers | {"X-Tenant-Id": "synthetic-other-tenant"},
        404,
    )
    call(
        "denied-read",
        "GET",
        f"/reports/jobs/{job_id}",
        headers | {"X-Capabilities": "reports.submit"},
        403,
        denial="missing_capability:reports.read",
    )
    monkeypatch.setenv("ENTERPRISE_AUDIT_READS", "false")
    call("read-audit-disabled", "GET", f"/reports/jobs/{job_id}", headers, 200, audited=False)
    reopened = ReportJobLedger(ledger_path)
    jobs = reopened.list_jobs(filters=ReportJobListFilters(limit=20))
    assert [job.job_id for job in jobs] == [job_id]
    assert jobs[0].tenant_id == headers["X-Tenant-Id"]
    assert jobs[0].triggered_by == headers["X-Actor-Id"]
    assert jobs[0].correlation_id == "audit-allowed"
    assert len(captures) == 12 and sum(len(item["events"]) for item in captures) == 11
    if target := os.getenv("REPORT_AUDIT_EVIDENCE_DIR"):
        path = Path(target)
        path.mkdir(parents=True, exist_ok=True)
        (path / "http-audit.json").write_text(
            json.dumps(
                {
                    "requests": captures,
                    "persisted_job_id": job_id,
                    "persisted_job_count": len(jobs),
                    "persisted_jobs": [job.model_dump(mode="json") for job in jobs],
                },
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
