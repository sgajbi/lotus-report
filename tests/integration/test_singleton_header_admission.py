"""Raw HTTP ambiguity is refused before actual PostgreSQL admission effects."""

import hashlib
import json
import os
import socket
from copy import deepcopy
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from uuid import uuid4

import httpx
import psycopg
import pytest
import uvicorn

from app.config import settings
from app.main import app
from app.postgres import close_postgres_connection_provider
from app.reporting_jobs.service import get_report_job_ledger
from tests.integration.test_report_job_api import _payload
from tests.unit.test_request_header_admission import SINGLETONS

TABLES = ("report_request", "report_job", "report_status_event", "report_job_work_item")


@pytest.fixture
def singleton_http_postgres(monkeypatch):
    database_url = os.getenv("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL is required for PostgreSQL header proof")
    monkeypatch.setattr(settings, "report_job_ledger_database_url", database_url)
    for name in ("ENTERPRISE_ENFORCE_AUTHZ", "ENTERPRISE_ENFORCE_READ_AUTHZ"):
        monkeypatch.setenv(name, "true")
    monkeypatch.setenv("ENTERPRISE_RUNTIME_PROFILE", "local")
    monkeypatch.setenv(
        "ENTERPRISE_CAPABILITY_RULES_JSON",
        json.dumps(
            {
                "POST /reports/portfolio-reviews": "reports.submit",
                "GET /reports/jobs": "reports.read",
            }
        ),
    )
    assert not app.dependency_overrides
    close_postgres_connection_provider()
    get_report_job_ledger.cache_clear()
    get_report_job_ledger()
    connection = psycopg.connect(database_url)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", log_config=None, lifespan="off"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]})
    owned = {}
    thread.start()
    try:
        deadline = monotonic() + 10
        while not server.started and thread.is_alive() and monotonic() < deadline:
            sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            yield client, connection, owned
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        close_postgres_connection_provider()
        get_report_job_ledger.cache_clear()
        owned_rows_removed = False
        connection.rollback()
        if job_id := owned.get("job_id"):
            for table in ("report_job_work_item", "report_status_event", "report_job"):
                connection.execute(f"DELETE FROM {table} WHERE report_job_id = %s", (job_id,))
            connection.execute(
                "DELETE FROM report_request WHERE report_request_id = %s", (owned["request_id"],)
            )
            connection.commit()
            for table in ("report_job_work_item", "report_status_event", "report_job"):
                assert (
                    connection.execute(
                        f"SELECT count(*) FROM {table} WHERE report_job_id = %s", (job_id,)
                    ).fetchone()[0]
                    == 0
                )
            assert (
                connection.execute(
                    "SELECT count(*) FROM report_request WHERE report_request_id = %s",
                    (owned["request_id"],),
                ).fetchone()[0]
                == 0
            )
            owned_rows_removed = True
        connection.close()
        assert not thread.is_alive()
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        if target := os.getenv("REPORT_HEADER_EVIDENCE_DIR"):
            path = Path(target)
            path.mkdir(parents=True, exist_ok=True)
            (path / "listener-closed.json").write_text(
                json.dumps(
                    {"report_listener_closed": True, "owned_job_removed": owned_rows_removed}
                ),
                encoding="utf-8",
            )


def _counts(connection):
    return {
        table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES
    }


def _state_fingerprint(connection, owned):
    if not owned:
        return None
    state = {}
    for table, field, value, order in (
        ("report_request", "report_request_id", owned["request_id"], "report_request_id"),
        ("report_job", "report_job_id", owned["job_id"], "report_job_id"),
        ("report_status_event", "report_job_id", owned["job_id"], "status_event_id"),
        ("report_job_work_item", "report_job_id", owned["job_id"], "work_item_id"),
    ):
        rows = connection.execute(
            f"SELECT row_to_json(record) FROM {table} record WHERE {field} = %s ORDER BY {order}",
            (value,),
        ).fetchall()
        state[table] = [row[0] for row in rows]
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_raw_header_admission_has_order_independent_postgres_no_effects(singleton_http_postgres):
    client, connection, owned = singleton_http_postgres
    token = uuid4().hex
    key = f"singleton-{token}"
    headers = {
        "X-Actor-Id": "synthetic-actor",
        "X-Tenant-Id": f"synthetic-tenant-{token}",
        "X-Caller-Application": "lotus-gateway",
        "X-Region": "APAC",
        "X-Booking-Center-Code": "SG",
        "X-Role": "advisor",
        "X-Correlation-ID": f"corr-{token}",
        "X-Service-Identity": "synthetic-service",
        "X-Capabilities": "reports.submit,reports.read",
        "Idempotency-Key": key,
    }
    payload = _payload()
    payload["portfolio_scope"]["portfolio_ids"] = ["SYNTHETIC_HEADER_ADMISSION"]
    evidence = []

    def call(name, method, path, raw, status, *, body=None, ambiguous=None):
        before = _counts(connection)
        fingerprint_before = _state_fingerprint(connection, owned)
        response = client.request(method, path, headers=raw, json=body)
        after = _counts(connection)
        fingerprint_after = _state_fingerprint(connection, owned)
        assert response.status_code == status
        if ambiguous:
            assert response.json() == {
                "detail": {"code": "ambiguous_request_headers", "headers": ambiguous}
            }
        if name != "allowed":
            assert after == before
            assert fingerprint_after == fingerprint_before
        evidence.append(
            {
                "name": name,
                "method": method,
                "path": path,
                "raw_headers": [
                    [
                        name.decode("latin-1"),
                        "***REDACTED***"
                        if name.lower() in {b"x-service-identity", b"authorization"}
                        else value.decode("latin-1"),
                    ]
                    for name, value in response.request.headers.raw
                ],
                "status": status,
                "response": response.json(),
                "counts_before": before,
                "counts_after": after,
                "owned_state_before": fingerprint_before,
                "owned_state_after": fingerprint_after,
            }
        )
        return response

    initial = _counts(connection)
    response = call(
        "allowed", "POST", "/reports/portfolio-reviews", list(headers.items()), 202, body=payload
    )
    job_id = response.json()["report_job_id"]
    owned.update(job_id=job_id, request_id=response.json()["report_request_id"])
    admitted_fingerprint = _state_fingerprint(connection, owned)
    evidence[0]["owned_state_after"] = admitted_fingerprint
    admitted = _counts(connection)
    assert admitted == {table: count + 1 for table, count in initial.items()}
    replay = call(
        "replay", "POST", "/reports/portfolio-reviews", list(headers.items()), 202, body=payload
    )
    assert replay.json()["report_job_id"] == job_id
    conflict = deepcopy(payload)
    conflict["reporting_currency"] = "SGD"
    call(
        "conflict", "POST", "/reports/portfolio-reviews", list(headers.items()), 409, body=conflict
    )
    call(
        "missing-key",
        "POST",
        "/reports/portfolio-reviews",
        [(name, value) for name, value in headers.items() if name != "Idempotency-Key"],
        400,
        body=payload,
    )
    call(
        "missing-service",
        "POST",
        "/reports/portfolio-reviews",
        [(name, value) for name, value in headers.items() if name != "X-Service-Identity"],
        403,
        body=payload,
    )
    call(
        "missing-tenant",
        "POST",
        "/reports/portfolio-reviews",
        [(name, value) for name, value in headers.items() if name != "X-Tenant-Id"],
        403,
        body=payload,
    )

    for header in SINGLETONS:
        for identical in (False, True):
            for reverse in (False, True):
                raw = [(name, value) for name, value in headers.items() if name.lower() != header]
                values = ["synthetic-a", "synthetic-a" if identical else "synthetic-b"]
                if reverse:
                    values.reverse()
                raw += [(header.upper(), values[0]), (header, values[1])]
                call(
                    f"duplicate:{header}:equal={identical}:reverse={reverse}",
                    "POST",
                    "/reports/portfolio-reviews",
                    raw,
                    400,
                    body=payload,
                    ambiguous=[header],
                )

    for reverse in (False, True):
        raw = [(name, value) for name, value in headers.items() if name != "Idempotency-Key"]
        values = [f"pair-a-{token}", f"pair-b-{token}"]
        if reverse:
            values.reverse()
        raw += [("Idempotency-Key", value) for value in values]
        call(
            f"two-key-reversal:{reverse}",
            "POST",
            "/reports/portfolio-reviews",
            raw,
            400,
            body=payload,
            ambiguous=["idempotency-key"],
        )
        for suffix in ("a", "b"):
            assert (
                connection.execute(
                    "SELECT count(*) FROM report_request "
                    "WHERE tenant_id = %s AND idempotency_key = %s",
                    (headers["X-Tenant-Id"], f"pair-{suffix}-{token}"),
                ).fetchone()[0]
                == 0
            )

    for reverse in (False, True):
        raw = [(name, value) for name, value in headers.items() if name != "X-Capabilities"]
        values = ["reports.read", "reports.submit"]
        if reverse:
            values.reverse()
        raw += [("X-Capabilities", values[0]), ("x-capabilities", values[1])]
        replay = call(
            f"capability-list:{reverse}",
            "POST",
            "/reports/portfolio-reviews",
            raw,
            202,
            body=payload,
        )
        assert replay.json()["report_job_id"] == job_id

    for method, path in (
        ("GET", f"/reports/jobs/{job_id}"),
        ("GET", f"/reports/jobs/{job_id}/events"),
        ("POST", f"/reports/jobs/{job_id}/replay"),
    ):
        for reverse in (False, True):
            raw = [(name, value) for name, value in headers.items() if name != "X-Tenant-Id"]
            values = [headers["X-Tenant-Id"], "synthetic-foreign-tenant"]
            if reverse:
                values.reverse()
            raw += [("X-Tenant-Id", values[0]), ("x-tenant-id", values[1])]
            call(
                f"scoped-route:{method}:{path}:reverse={reverse}",
                method,
                path,
                raw,
                400,
                body={} if method == "POST" else None,
                ambiguous=["x-tenant-id"],
            )
    call("owned-read", "GET", f"/reports/jobs/{job_id}", list(headers.items()), 200)
    call(
        "foreign-read",
        "GET",
        f"/reports/jobs/{job_id}",
        list((headers | {"X-Tenant-Id": "synthetic-foreign-tenant"}).items()),
        404,
    )
    call(
        "foreign-region-read",
        "GET",
        f"/reports/jobs/{job_id}",
        list((headers | {"X-Region": "EMEA"}).items()),
        404,
    )
    row = connection.execute(
        "SELECT tenant_id, region, triggered_by, caller_application, correlation_id "
        "FROM report_request WHERE report_request_id = %s",
        (owned["request_id"],),
    ).fetchone()
    assert row == (
        headers["X-Tenant-Id"],
        "APAC",
        "synthetic-actor",
        "lotus-gateway",
        headers["X-Correlation-ID"],
    )
    assert _counts(connection) == admitted
    assert _state_fingerprint(connection, owned) == admitted_fingerprint
    assert (
        connection.execute(
            "SELECT status FROM report_job WHERE report_job_id = %s", (job_id,)
        ).fetchone()[0]
        == "accepted"
    )
    assert len(evidence) == 71
    assert connection.execute(
        "SELECT status, attempt_count, lease_owner, lease_token "
        "FROM report_job_work_item WHERE report_job_id = %s",
        (job_id,),
    ).fetchone() == ("pending", 0, None, None)
    if target := os.getenv("REPORT_HEADER_EVIDENCE_DIR"):
        path = Path(target)
        path.mkdir(parents=True, exist_ok=True)
        (path / "http-headers.json").write_text(
            json.dumps(
                {
                    "requests": evidence,
                    "initial_counts": initial,
                    "admitted_counts": admitted,
                    "final_counts": _counts(connection),
                    "owned_job_id": job_id,
                    "owned_request_id": owned["request_id"],
                    "admitted_state_fingerprint": admitted_fingerprint,
                },
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
