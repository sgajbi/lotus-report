"""Registered socket correction flows, durable SQLite and controlled Archive HTTP."""

import asyncio
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import httpx
import pytest
import uvicorn

from app.clients.archive_client import ArchiveClient
from app.main import app
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.service import get_portfolio_review_snapshot_capture_service
from app.reporting_render.archive_lineage import reconcile_pending_archive_lineage
from tests.integration.test_report_job_api import (
    _clear_overrides,
    _client,
    _create_archived_pdf_job,
    _FakeCaptureService,
    _headers,
    _install_regenerate_service,
    _install_rerender_service,
    _RerenderRenderClient,
)

TRACE = "0123456789abcdef0123456789abcdef"
CORRELATION = "archive-ack-correlation"


def _acknowledgement(source, target, transition, fault):
    payload = {
        "lifecycle_relationship_id": "relationship-original",
        "source_document_id": source,
        "target_document_id": target,
        "transition_type": transition,
        "transition_reason": "Original accepted reason",
        "transition_reason_code": "report_correction",
        "requested_by": "original-actor",
        "requested_at": "2026-01-09T00:00:00Z",
        "current_document_id": "doc_later_in_chain",
    }
    if fault == "empty":
        return {}
    if fault == "missing-id":
        payload.pop("lifecycle_relationship_id")
    if fault in {"foreign-source", "foreign-target", "foreign-transition"}:
        key = {
            "foreign-source": "source_document_id",
            "foreign-target": "target_document_id",
            "foreign-transition": "transition_type",
        }[fault]
        payload[key] = "unrelated"
    return payload


@pytest.fixture(scope="module")
def archive_http_world():
    state = {"status": 201, "fault": None, "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            _, documents, source_id, transition = self.path.split("/")
            assert documents == "documents" and transition in {"correct", "supersede"}
            payload = _acknowledgement(
                source_id, body["target_document_id"], transition, state["fault"]
            )
            content = (
                b"<html>untrusted-source-detail</html>"
                if state["fault"] == "html"
                else json.dumps(payload, allow_nan=False).encode()
            )
            state["calls"].append(
                {
                    "path": self.path,
                    "body": body,
                    "headers": dict(self.headers),
                    "status": state["status"],
                    "payload": (
                        "<html>untrusted-source-detail</html>"
                        if state["fault"] == "html"
                        else payload
                    ),
                }
            )
            self.send_response(state["status"])
            self.send_header(
                "Content-Type", "text/html" if state["fault"] == "html" else "application/json"
            )
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    source = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    source_thread = Thread(target=source.serve_forever)
    source_thread.start()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    report_port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    report_thread = Thread(target=server.run, kwargs={"sockets": [listener]})
    report_thread.start()
    try:
        deadline = monotonic() + 10
        while not server.started and report_thread.is_alive() and monotonic() < deadline:
            sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{report_port}", timeout=10) as client:
            yield (
                state,
                client,
                ArchiveClient(
                    base_url=f"http://127.0.0.1:{source.server_port}",
                    timeout_seconds=3,
                    max_retries=0,
                    retry_backoff_seconds=0,
                ),
            )
    finally:
        _clear_overrides()
        server.should_exit = True
        report_thread.join(timeout=10)
        listener.close()
        source.shutdown()
        source.server_close()
        source_thread.join(timeout=10)
        assert not report_thread.is_alive() and not source_thread.is_alive()
        for port in (report_port, source.server_port):
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", port)) != 0
        if capture := os.getenv("REPORT_ARCHIVE_ACK_EVIDENCE_DIR"):
            target = Path(capture)
            target.mkdir(parents=True, exist_ok=True)
            (target / "listeners-closed.json").write_text(
                json.dumps(
                    {
                        "report_listener_closed": True,
                        "source_listener_closed": True,
                    }
                ),
                encoding="utf-8",
            )


def _lineage_events(ledger, job_id):
    return [
        event.model_dump(mode="json")
        for event in ledger.list_status_events(job_id)
        if event.event_type.startswith("job_archive_lineage_")
    ]


def _assert_pair(ledger, job_id, transition, target, *, confirmed):
    events = _lineage_events(ledger, job_id)
    assert [event["event_type"] for event in events] == (
        ["job_archive_lineage_recorded"]
        if confirmed and len(events) == 1
        else ["job_archive_lineage_pending", "job_archive_lineage_recorded"]
        if confirmed
        else ["job_archive_lineage_pending"]
    )
    for event in events:
        payload = event["event_payload"]
        assert payload["source_document_id"] == "doc_report_job_pdf"
        assert payload["target_document_id"] == target and payload["transition_type"] == transition
        assert event["event_idempotency_key"].endswith(f"{transition}:doc_report_job_pdf->{target}")
        assert "untrusted-source-detail" not in json.dumps(event)
        if event["event_type"] == "job_archive_lineage_recorded":
            assert payload["lifecycle_relationship_id"] == "relationship-original"
            assert "transition_reason" not in payload and "current_document_id" not in payload
        else:
            assert payload["reason_code"] == (
                "archive_lineage_acknowledgement_invalid"
                if payload["status_code"] in {200, 201}
                else "archive_lineage_call_unconfirmed"
            )
    assert bool(ledger.list_pending_archive_lineage(limit=10)) is not confirmed
    return events


@pytest.mark.parametrize(
    "command,transition", [("rerender", "correct"), ("regenerate", "supersede")]
)
@pytest.mark.parametrize("status", [200, 201])
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "empty",
        "missing-id",
        "foreign-source",
        "foreign-target",
        "foreign-transition",
        "html",
        "outage-then-empty",
    ],
)
def test_registered_correction_acknowledgement_remains_pending_until_valid(
    tmp_path, archive_http_world, command, transition, status, fault
):
    state, client, archive = archive_http_world
    state.update(
        status=503 if fault == "outage-then-empty" else status,
        fault="empty" if fault == "outage-then-empty" else fault,
        calls=[],
    )
    configured_client, ledger, store = _client(tmp_path)
    configured_client.close()
    target_document = (
        f"doc_report_job_pdf_{'correction' if command == 'rerender' else 'replacement'}"
    )
    render = _RerenderRenderClient(archive_document_id=target_document)
    capture = _FakeCaptureService(ledger, store)
    app.dependency_overrides[get_portfolio_review_snapshot_capture_service] = lambda: capture
    if command == "rerender":
        _install_rerender_service(ledger, store, render, archive)
    else:
        _install_regenerate_service(ledger, store, capture, render, archive)
    try:
        source = _create_archived_pdf_job(client, ledger)
        snapshot = store.get_snapshot_by_job(source.job_id)
        snapshot_bytes = json.dumps(snapshot.snapshot_payload, sort_keys=True)
        headers = _headers(f"archive-ack-{command}") | {
            "X-Correlation-ID": CORRELATION,
            "X-Trace-ID": TRACE,
        }
        response = client.post(
            f"/reports/jobs/{source.job_id}/{command}",
            json={"reason": "Certified correction."},
            headers=headers,
        )
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "archived"
        assert response.headers["X-Correlation-Id"] == CORRELATION
        assert response.headers["X-Trace-Id"] == TRACE
        json.dumps(response.json(), allow_nan=False)
        assert len(state["calls"]) == 1 and len(render.payloads) == 1
        call = state["calls"][0]
        assert call["path"] == f"/documents/doc_report_job_pdf/{transition}"
        assert set(call["body"]) == {"target_document_id", "transition_reason"}
        assert call["body"]["target_document_id"] == target_document
        source_headers = httpx.Headers(call["headers"])
        assert source_headers["X-Tenant-Id"] == "tenant-sg"
        assert source_headers["X-Region"] == "APAC"
        assert source_headers["X-Actor-Id"] == "advisor-123"
        assert source_headers["X-Booking-Center-Code"] == "SG"
        assert source_headers["X-Role"] == "advisor"
        assert source_headers["X-Correlation-ID"] == CORRELATION
        assert source_headers["X-Trace-ID"] == TRACE
        assert source_headers["traceparent"].startswith(f"00-{TRACE}-")
        phases = [
            _assert_pair(
                ledger, source.job_id, transition, target_document, confirmed=fault is None
            )
        ]
        # Recreate the native adapter after each stage; no direct database edits.
        reopened = ReportJobLedger(tmp_path / "jobs.sqlite3")
        if fault is not None:
            state["status"] = status
            result = asyncio.run(
                reconcile_pending_archive_lineage(archive_client=archive, ledger=reopened, limit=10)
            )
            assert result["attempted_jobs"] == 1 and len(state["calls"]) == 2
            phases.append(
                _assert_pair(reopened, source.job_id, transition, target_document, confirmed=False)
            )
            state["fault"] = None
            reopened = ReportJobLedger(tmp_path / "jobs.sqlite3")
            result = asyncio.run(
                reconcile_pending_archive_lineage(archive_client=archive, ledger=reopened, limit=10)
            )
            assert result["attempted_jobs"] == 1 and len(state["calls"]) == 3
            phases.append(
                _assert_pair(reopened, source.job_id, transition, target_document, confirmed=True)
            )
        reopened = ReportJobLedger(tmp_path / "jobs.sqlite3")
        before = len(state["calls"])
        result = asyncio.run(
            reconcile_pending_archive_lineage(archive_client=archive, ledger=reopened, limit=10)
        )
        assert result["attempted_jobs"] == 0 and len(state["calls"]) == before
        app.dependency_overrides[get_report_job_ledger] = lambda: reopened
        public_events = client.get(f"/reports/jobs/{source.job_id}/events", headers=headers)
        assert public_events.status_code == 200
        assert "untrusted-source-detail" not in public_events.text
        assert "relationship-original" in public_events.text
        assert reopened.get_job(source.job_id).archive_document_id == source.archive_document_id
        assert reopened.get_job(source.job_id).status == "archived"
        retained = store.get_snapshot_by_job(source.job_id)
        assert retained.snapshot_hash == snapshot.snapshot_hash
        assert json.dumps(retained.snapshot_payload, sort_keys=True) == snapshot_bytes
        assert len(render.payloads) == 1
        assert len(reopened.list_rerender_attempts(source.job_id)) == (
            1 if command == "rerender" else 0
        )
        assert len(reopened.list_job_relationships(source.job_id)) == (
            1 if command == "regenerate" else 0
        )
        if evidence_dir := os.getenv("REPORT_ARCHIVE_ACK_EVIDENCE_DIR"):
            directory = Path(evidence_dir)
            directory.mkdir(parents=True, exist_ok=True)
            record = {
                "command": command,
                "transition": transition,
                "fault": fault,
                "status": status,
                "public_url": str(response.request.url),
                "response": response.json(),
                "response_status": response.status_code,
                "source_calls": state["calls"],
                "phases": phases,
                "public_events": public_events.json(),
                "original_snapshot_hash": snapshot.snapshot_hash,
                "retained_snapshot_hash": retained.snapshot_hash,
                "snapshot_bytes_unchanged": True,
                "render_calls": len(render.payloads),
                "pending_jobs_final": len(reopened.list_pending_archive_lineage(limit=10)),
                "final_reconciliation_attempts": result["attempted_jobs"],
            }
            (directory / f"case-{len(list(directory.glob('case-*.json'))):03}.json").write_text(
                json.dumps(record, allow_nan=False, indent=2),
                encoding="utf-8",
            )
    finally:
        _clear_overrides()
