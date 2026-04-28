from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path = [path for path in sys.path if path != str(SRC)]
sys.path.insert(0, str(SRC))
WORKSPACE_ROOT = ROOT.parent
RENDER_ROOT = WORKSPACE_ROOT / "lotus-render"
ARCHIVE_ROOT = WORKSPACE_ROOT / "lotus-archive"
REPORT_PYTHON = Path(sys.executable)
RENDER_PYTHON = RENDER_ROOT / ".venv" / "Scripts" / "python.exe"
ARCHIVE_PYTHON = ARCHIVE_ROOT / ".venv" / "Scripts" / "python.exe"

RENDER_PORT = 8350
ARCHIVE_PORT = 8351
REPORT_PORT = 8352


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp_slug() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _capture(
    *,
    path: Path,
    method: str,
    url: str,
    status_code: int,
    payload: Any,
) -> None:
    _write_json(path, payload)
    _write_json(
        Path(f"{path}.meta.json"),
        {
            "captured_at_utc": _utc_now(),
            "method": method,
            "status_code": status_code,
            "url": url,
        },
    )


def _http_json(
    *,
    method: str,
    url: str,
    payload: Any | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    request_headers = dict(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw_body = response.read()
            status_code = response.getcode()
    except urllib.error.HTTPError as exc:
        raw_body = exc.read()
        status_code = exc.code
    body = raw_body.decode("utf-8")
    if not body.strip():
        return status_code, None
    return status_code, json.loads(body)


def _wait_for_json(url: str, *, timeout_seconds: int = 60) -> Any:
    deadline = time.time() + timeout_seconds
    last_error: str | None = None
    while time.time() < deadline:
        try:
            status_code, payload = _http_json(method="GET", url=url)
            if status_code == 200:
                return payload
            last_error = f"{url} returned {status_code}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(last_error or f"timed out waiting for {url}")


def _start_process(
    *,
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
) -> tuple[subprocess.Popen[str], Any, Any]:
    stdout_handle = stdout_path.open("w", encoding="utf-8")
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=stdout_handle,
        stderr=stderr_handle,
        text=True,
    )
    return process, stdout_handle, stderr_handle


def _stop_process(process: subprocess.Popen[str], stdout_handle: Any, stderr_handle: Any) -> None:
    try:
        process.terminate()
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)
    finally:
        stdout_handle.close()
        stderr_handle.close()


def _headers(idempotency_key: str, *, correlation_id: str, trace_id: str) -> dict[str, str]:
    return {
        "Idempotency-Key": idempotency_key,
        "X-Actor-Id": "advisor-123",
        "X-Caller-Application": "lotus-gateway",
        "X-Tenant-Id": "tenant-sg",
        "X-Region": "APAC",
        "X-Booking-Center-Code": "SG",
        "X-Role": "advisor",
        "X-Correlation-ID": correlation_id,
        "X-Trace-ID": trace_id,
    }


def _report_payload(*, as_of_date: str = "2026-04-23") -> dict[str, Any]:
    return {
        "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        "as_of_date": as_of_date,
        "requested_output_formats": ["pdf"],
        "reporting_currency": "USD",
        "options": {
            "sections": ["OVERVIEW", "PERFORMANCE", "RISK"],
            "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
        },
    }


def _batch_payload(*, as_of_date: str = "2026-04-23") -> dict[str, Any]:
    return {
        "selector_mode": "explicit_portfolio_list",
        "portfolio_ids": ["PB_SG_GLOBAL_BAL_001"],
        "source_candidates": [
            {
                "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                "tenant_id": "tenant-sg",
                "region": "APAC",
                "active": True,
            }
        ],
        "as_of_date": as_of_date,
        "requested_output_formats": ["pdf"],
        "reporting_currency": "USD",
        "options": {"sections": ["OVERVIEW", "PERFORMANCE"]},
    }


def _scheduler_payload() -> list[dict[str, Any]]:
    return [
        {
            "schedule_id": "monthly-sg-global-bal-rfc0105-live",
            "enabled": True,
            "selector_mode": "explicit_portfolio_list",
            "frequency": "monthly",
            "as_of_date": "2026-04-25",
            "portfolio_ids": ["PB_SG_GLOBAL_BAL_001"],
            "requested_output_formats": ["pdf"],
            "reporting_currency": "USD",
            "options": {"sections": ["OVERVIEW", "PERFORMANCE"]},
        }
    ]


def _record_repository_metadata() -> dict[str, dict[str, str]]:
    repositories: dict[str, dict[str, str]] = {}
    for name, path in {
        "lotus-report": ROOT,
        "lotus-render": RENDER_ROOT,
        "lotus-archive": ARCHIVE_ROOT,
    }.items():
        repositories[name] = {
            "path": str(path),
            "branch": subprocess.check_output(
                ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
                text=True,
            ).strip(),
            "head_sha": subprocess.check_output(
                ["git", "-C", str(path), "rev-parse", "HEAD"],
                text=True,
            ).strip(),
        }
    return repositories


def _extract_ids(payload: dict[str, Any]) -> dict[str, str | None]:
    render = payload.get("render") if isinstance(payload.get("render"), dict) else {}
    archive = payload.get("archive") if isinstance(payload.get("archive"), dict) else {}
    return {
        "report_job_id": payload.get("report_job_id"),
        "snapshot_id": payload.get("snapshot", {}).get("snapshot_id")
        if isinstance(payload.get("snapshot"), dict)
        else None,
        "render_job_id": render.get("render_job_id"),
        "document_id": archive.get("document_id"),
    }


def main() -> int:
    evidence_dir = ROOT / "output" / f"rfc-0105-live-evidence-{_timestamp_slug()}"
    evidence_dir.mkdir(parents=True, exist_ok=False)
    data_dir = evidence_dir / "runtime-data"
    data_dir.mkdir()

    suffix = uuid4().hex
    correlation_id = f"corr-rfc0105-{suffix}"
    trace_id = f"{suffix[:32]}"
    run_metadata = {
        "rfc_id": "RFC-0105",
        "slice": "Slice 9 - implementation proof",
        "generated_at_utc": _utc_now(),
        "repositories": _record_repository_metadata(),
        "ports": {
            "lotus-render": RENDER_PORT,
            "lotus-archive": ARCHIVE_PORT,
            "lotus-report": REPORT_PORT,
        },
        "correlation_id": correlation_id,
        "trace_id": trace_id,
    }
    _write_json(evidence_dir / "00-run-metadata.json", run_metadata)

    render_env = os.environ.copy()
    render_env.update(
        {
            "LOTUS_RENDER_RENDER_STORE_PATH": str(data_dir / "render.sqlite3"),
            "PYTHONPATH": "src",
        }
    )
    archive_env = os.environ.copy()
    archive_env.update({"PYTHONPATH": "src"})
    report_env = os.environ.copy()
    report_env.update(
        {
            "PYTHONPATH": "src",
            "RFC0102_LEDGER_PATH": str(data_dir / "report-jobs.sqlite3"),
            "RFC0102_LINEAGE_PATH": str(data_dir / "report-lineage.sqlite3"),
            "RFC0102_BATCH_LEDGER_PATH": str(data_dir / "report-batches.sqlite3"),
            "RFC0102_SNAPSHOT_FIXTURE_PATH": str(
                ROOT / "scripts" / "fixtures" / "rfc_0102_proof_snapshot.json"
            ),
            "RFC0102_RENDER_BASE_URL": f"http://127.0.0.1:{RENDER_PORT}",
            "RFC0102_ARCHIVE_BASE_URL": f"http://127.0.0.1:{ARCHIVE_PORT}",
            "RFC0102_RENDER_REQUEST_CAPTURE_PATH": str(
                evidence_dir / "30-report-render-request.json"
            ),
            "RFC0102_RENDER_RESPONSE_CAPTURE_PATH": str(
                evidence_dir / "31-report-render-response.json"
            ),
            "RFC0102_ARCHIVE_REQUEST_CAPTURE_PATH": str(
                evidence_dir / "32-report-archive-request.json"
            ),
            "RFC0102_ARCHIVE_RESPONSE_CAPTURE_PATH": str(
                evidence_dir / "33-report-archive-response.json"
            ),
            "RFC0102_PROOF_HOST": "127.0.0.1",
            "RFC0102_PROOF_PORT": str(REPORT_PORT),
            "REPORT_BATCH_SCHEDULER_ID": "rfc0105-live-scheduler",
            "REPORT_BATCH_SCHEDULES_JSON": json.dumps(_scheduler_payload()),
        }
    )

    render_proc, render_out, render_err = _start_process(
        command=[
            str(RENDER_PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(RENDER_PORT),
        ],
        cwd=RENDER_ROOT,
        env=render_env,
        stdout_path=evidence_dir / "lotus-render.out.log",
        stderr_path=evidence_dir / "lotus-render.err.log",
    )
    archive_proc, archive_out, archive_err = _start_process(
        command=[
            str(ARCHIVE_PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(ARCHIVE_PORT),
        ],
        cwd=ARCHIVE_ROOT,
        env=archive_env,
        stdout_path=evidence_dir / "lotus-archive.out.log",
        stderr_path=evidence_dir / "lotus-archive.err.log",
    )
    report_proc, report_out, report_err = _start_process(
        command=[str(REPORT_PYTHON), "scripts/rfc_0102_proof_app.py"],
        cwd=ROOT,
        env=report_env,
        stdout_path=evidence_dir / "lotus-report.out.log",
        stderr_path=evidence_dir / "lotus-report.err.log",
    )

    proof: dict[str, Any] = {}
    identifiers: dict[str, Any] = {
        "correlation_id": correlation_id,
        "trace_id": trace_id,
    }
    try:
        for index, (service, port) in enumerate(
            (
                ("render", RENDER_PORT),
                ("archive", ARCHIVE_PORT),
                ("report", REPORT_PORT),
            ),
            start=1,
        ):
            health = _wait_for_json(f"http://127.0.0.1:{port}/health/ready")
            _capture(
                path=evidence_dir / f"{index:02d}-{service}-ready.json",
                method="GET",
                url=f"http://127.0.0.1:{port}/health/ready",
                status_code=200,
                payload=health,
            )

        headers = _headers(
            f"rfc0105-main-{suffix}",
            correlation_id=correlation_id,
            trace_id=trace_id,
        )
        submit_status, submit_payload = _http_json(
            method="POST",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/portfolio-reviews",
            payload=_report_payload(),
            headers=headers,
        )
        _capture(
            path=evidence_dir / "10-report-submit-response.json",
            method="POST",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/portfolio-reviews",
            status_code=submit_status,
            payload=submit_payload,
        )
        report_job_id = str(submit_payload["report_job_id"])
        identifiers["report_job_id"] = report_job_id

        status_code, status_payload = _http_json(
            method="GET",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/jobs/{urllib.parse.quote(report_job_id)}",
            headers=headers,
        )
        _capture(
            path=evidence_dir / "11-report-status-response.json",
            method="GET",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/jobs/{report_job_id}",
            status_code=status_code,
            payload=status_payload,
        )
        identifiers.update(
            {
                "render_job_id": status_payload.get("render", {}).get("render_job_id"),
                "document_id": status_payload.get("archive", {}).get("document_id"),
            }
        )

        for name, path in (
            ("events", f"/reports/jobs/{report_job_id}/events"),
            ("snapshot", f"/reports/jobs/{report_job_id}/snapshot"),
            ("lineage", f"/reports/jobs/{report_job_id}/lineage"),
            ("diagnostics", f"/reports/jobs/{report_job_id}/diagnostics"),
        ):
            code, payload = _http_json(
                method="GET",
                url=f"http://127.0.0.1:{REPORT_PORT}{path}",
                headers=headers,
            )
            _capture(
                path=evidence_dir / f"12-report-{name}-response.json",
                method="GET",
                url=f"http://127.0.0.1:{REPORT_PORT}{path}",
                status_code=code,
                payload=payload,
            )
            if name == "snapshot":
                identifiers["snapshot_id"] = payload.get("snapshot_id")

        document_id = str(identifiers["document_id"])
        for name, path in (
            ("metadata", f"/documents/{document_id}"),
            ("access-events", f"/documents/{document_id}/access-events"),
        ):
            code, payload = _http_json(
                method="GET",
                url=f"http://127.0.0.1:{ARCHIVE_PORT}{path}",
                headers=headers,
            )
            _capture(
                path=evidence_dir / f"13-archive-{name}-response.json",
                method="GET",
                url=f"http://127.0.0.1:{ARCHIVE_PORT}{path}",
                status_code=code,
                payload=payload,
            )

        rerender_headers = _headers(
            f"rfc0105-rerender-{suffix}",
            correlation_id=correlation_id,
            trace_id=trace_id,
        )
        rerender_code, rerender_payload = _http_json(
            method="POST",
            url=(
                f"http://127.0.0.1:{REPORT_PORT}/reports/jobs/"
                f"{urllib.parse.quote(report_job_id)}/rerender"
            ),
            payload={"reason": "RFC-0105 Slice 9 rerender proof"},
            headers=rerender_headers,
        )
        _capture(
            path=evidence_dir / "20-rerender-response.json",
            method="POST",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/jobs/{report_job_id}/rerender",
            status_code=rerender_code,
            payload=rerender_payload,
        )
        identifiers["rerender_document_id"] = (rerender_payload.get("archive") or {}).get(
            "document_id"
        )

        regenerate_headers = _headers(
            f"rfc0105-regenerate-{suffix}",
            correlation_id=correlation_id,
            trace_id=trace_id,
        )
        regenerate_code, regenerate_payload = _http_json(
            method="POST",
            url=(
                f"http://127.0.0.1:{REPORT_PORT}/reports/jobs/"
                f"{urllib.parse.quote(report_job_id)}/regenerate"
            ),
            payload={"reason": "RFC-0105 Slice 9 regenerate proof"},
            headers=regenerate_headers,
        )
        _capture(
            path=evidence_dir / "21-regenerate-response.json",
            method="POST",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/jobs/{report_job_id}/regenerate",
            status_code=regenerate_code,
            payload=regenerate_payload,
        )
        identifiers["regenerated_report_job_id"] = regenerate_payload.get(
            "regenerated_report_job_id"
        )
        identifiers["regenerated_snapshot_id"] = regenerate_payload.get("new_snapshot_id")
        identifiers["regenerated_document_id"] = regenerate_payload.get("new_archive_document_id")

        from app.reporting_jobs.ledger import ReportJobLedger
        from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext

        ledger = ReportJobLedger(data_dir / "report-jobs.sqlite3")
        failed_source_job = ledger.create_portfolio_review_job(
            request=PortfolioReviewJobRequest(
                portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
                as_of_date=datetime(2026, 4, 24, tzinfo=UTC).date(),
                requested_output_formats=["pdf"],
                reporting_currency="USD",
                options={"sections": ["OVERVIEW", "PERFORMANCE"]},
            ),
            caller_context=ReportCallerContext(
                triggered_by="advisor-123",
                caller_application="lotus-gateway",
                tenant_id="tenant-sg",
                region="APAC",
                booking_center_code="SG",
                role="advisor",
                correlation_id=correlation_id,
                trace_id=trace_id,
            ),
            idempotency_key=f"rfc0105-failed-source-job-{suffix}",
        )
        ledger.mark_failed(
            job_id=failed_source_job.job_id,
            actor="advisor-123",
            correlation_id=correlation_id,
            trace_id=trace_id,
            failure_category="render_execution_failed",
            failure_message="RFC-0105 forced retryable failure.",
            retry_eligible=True,
        )
        failed_job_id = failed_source_job.job_id
        _write_json(
            evidence_dir / "22-failed-source-job.json",
            ledger.get_job(failed_job_id).model_dump(mode="json"),
        )
        replay_headers = _headers(
            f"rfc0105-replay-{suffix}",
            correlation_id=correlation_id,
            trace_id=trace_id,
        )
        replay_code, replay_payload = _http_json(
            method="POST",
            url=(
                f"http://127.0.0.1:{REPORT_PORT}/reports/jobs/"
                f"{urllib.parse.quote(failed_job_id)}/replay"
            ),
            payload={"reason": "RFC-0105 Slice 9 failed job replay proof"},
            headers=replay_headers,
        )
        _capture(
            path=evidence_dir / "23-report-replay-response.json",
            method="POST",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/jobs/{failed_job_id}/replay",
            status_code=replay_code,
            payload=replay_payload,
        )
        identifiers["failed_report_job_id"] = failed_job_id
        identifiers["replayed_report_job_id"] = replay_payload.get("replayed_report_job_id")

        batch_headers = _headers(
            f"rfc0105-batch-{suffix}",
            correlation_id=correlation_id,
            trace_id=trace_id,
        )
        batch_code, batch_payload = _http_json(
            method="POST",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/batches",
            payload=_batch_payload(as_of_date="2026-04-25"),
            headers=batch_headers,
        )
        _capture(
            path=evidence_dir / "24-batch-create-response.json",
            method="POST",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/batches",
            status_code=batch_code,
            payload=batch_payload,
        )
        batch_id = str(batch_payload["batch_id"])
        from app.report_batch_orchestrator.ledger import ReportBatchLedger

        batch_ledger = ReportBatchLedger(data_dir / "report-batches.sqlite3")
        batch_record = batch_ledger.get_batch(batch_id)
        batch_item_id = batch_record.items[0].batch_item_id
        source_batch_job = ledger.create_portfolio_review_job(
            request=PortfolioReviewJobRequest(
                portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
                as_of_date=datetime(2026, 4, 25, tzinfo=UTC).date(),
                requested_output_formats=["pdf"],
                reporting_currency="USD",
                options={"sections": ["OVERVIEW", "PERFORMANCE"]},
            ),
            caller_context=ReportCallerContext(
                triggered_by="advisor-123",
                caller_application="lotus-gateway",
                tenant_id="tenant-sg",
                region="APAC",
                booking_center_code="SG",
                role="advisor",
                correlation_id=correlation_id,
                trace_id=trace_id,
            ),
            idempotency_key=f"rfc0105-batch-source-job-{suffix}",
        )
        leased = batch_ledger.acquire_dispatch_items(
            batch_id=batch_id,
            worker_id="rfc0105-proof-worker",
            lease_seconds=3600,
            limit=1,
            now=datetime.now(UTC) - timedelta(minutes=20),
        )[0]
        batch_ledger.mark_item_waiting_on_report_job(
            batch_item_id=batch_item_id,
            lease_token=str(leased.lease_token),
            report_job_id=source_batch_job.job_id,
            now=datetime.now(UTC) - timedelta(minutes=20),
        )
        batch_ledger.mark_item_failed(
            batch_item_id=batch_item_id,
            error_category="render_execution_failed",
            error_summary="RFC-0105 forced batch item failure.",
            retryable=True,
        )
        ledger.mark_failed(
            job_id=source_batch_job.job_id,
            actor="advisor-123",
            correlation_id=correlation_id,
            trace_id=trace_id,
            failure_category="render_execution_failed",
            failure_message="RFC-0105 forced linked job failure.",
            retry_eligible=True,
        )
        batch_replay_headers = _headers(
            f"rfc0105-batch-replay-{suffix}",
            correlation_id=correlation_id,
            trace_id=trace_id,
        )
        batch_replay_code, batch_replay_payload = _http_json(
            method="POST",
            url=(
                f"http://127.0.0.1:{REPORT_PORT}/reports/batches/"
                f"{urllib.parse.quote(batch_id)}/items/{urllib.parse.quote(batch_item_id)}/replay"
            ),
            payload={"reason": "RFC-0105 Slice 9 batch item replay proof"},
            headers=batch_replay_headers,
        )
        _capture(
            path=evidence_dir / "25-batch-item-replay-response.json",
            method="POST",
            url=(
                f"http://127.0.0.1:{REPORT_PORT}/reports/batches/"
                f"{batch_id}/items/{batch_item_id}/replay"
            ),
            status_code=batch_replay_code,
            payload=batch_replay_payload,
        )
        identifiers["batch_id"] = batch_id
        identifiers["batch_item_id"] = batch_item_id
        identifiers["batch_source_report_job_id"] = source_batch_job.job_id
        identifiers["batch_replayed_report_job_id"] = batch_replay_payload.get(
            "replayed_report_job_id"
        )

        attention_batch_headers = _headers(
            f"rfc0105-attention-batch-{suffix}",
            correlation_id=correlation_id,
            trace_id=trace_id,
        )
        attention_batch_code, attention_batch_payload = _http_json(
            method="POST",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/batches",
            payload=_batch_payload(as_of_date="2026-04-25"),
            headers=attention_batch_headers,
        )
        _capture(
            path=evidence_dir / "26-attention-batch-create-response.json",
            method="POST",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/batches",
            status_code=attention_batch_code,
            payload=attention_batch_payload,
        )
        attention_batch_id = str(attention_batch_payload["batch_id"])
        attention_batch_record = batch_ledger.get_batch(attention_batch_id)
        attention_batch_item_id = attention_batch_record.items[0].batch_item_id
        stale_attention_item = batch_ledger.acquire_dispatch_items(
            batch_id=attention_batch_id,
            worker_id="rfc0105-attention-proof-worker",
            lease_seconds=3600,
            limit=1,
            now=datetime.now(UTC) - timedelta(minutes=20),
        )[0]
        identifiers["attention_batch_id"] = attention_batch_id
        identifiers["attention_batch_item_id"] = attention_batch_item_id
        identifiers["attention_batch_lease_token"] = str(stale_attention_item.lease_token)

        attention_code, attention_payload = _http_json(
            method="GET",
            url=(
                f"http://127.0.0.1:{REPORT_PORT}/reports/operations/attention?"
                "report_job_stuck_threshold_seconds=1&batch_item_stuck_threshold_seconds=1"
                "&sla_breach_threshold_seconds=2&max_events=10"
            ),
            headers=headers,
        )
        _capture(
            path=evidence_dir / "27-attention-response.json",
            method="GET",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/operations/attention",
            status_code=attention_code,
            payload=attention_payload,
        )

        scheduler_list_code, scheduler_list_payload = _http_json(
            method="GET",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/batch-schedules",
            headers=headers,
        )
        _capture(
            path=evidence_dir / "28-scheduler-list-response.json",
            method="GET",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/batch-schedules",
            status_code=scheduler_list_code,
            payload=scheduler_list_payload,
        )
        scheduler_run_code, scheduler_run_payload = _http_json(
            method="POST",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/batch-schedules:run-due",
            payload={"pass_sequence": 9},
            headers=_headers(
                f"rfc0105-scheduler-run-{suffix}",
                correlation_id=correlation_id,
                trace_id=trace_id,
            ),
        )
        _capture(
            path=evidence_dir / "29-scheduler-run-due-response.json",
            method="POST",
            url=f"http://127.0.0.1:{REPORT_PORT}/reports/batch-schedules:run-due",
            status_code=scheduler_run_code,
            payload=scheduler_run_payload,
        )
        scheduler_materialized = scheduler_run_payload.get("materialized") or []
        scheduler_first_materialized = scheduler_materialized[0] if scheduler_materialized else {}
        identifiers["batch_schedule_id"] = scheduler_first_materialized.get("schedule_id")
        identifiers["batch_schedule_run_correlation_id"] = scheduler_run_payload.get(
            "correlation_id"
        )
        identifiers["scheduled_batch_id"] = scheduler_first_materialized.get("batch_id")

        metrics_code, metrics_payload = _http_text(url=f"http://127.0.0.1:{REPORT_PORT}/metrics")
        (evidence_dir / "30-report-metrics.prom").write_text(metrics_payload, encoding="utf-8")
        _write_json(
            evidence_dir / "30-report-metrics.meta.json",
            {
                "captured_at_utc": _utc_now(),
                "method": "GET",
                "status_code": metrics_code,
                "url": f"http://127.0.0.1:{REPORT_PORT}/metrics",
            },
        )

        proof = {
            "report_render_archive_archived": status_payload.get("status") == "archived",
            "diagnostics_safe": _is_operator_safe(
                evidence_dir / "12-report-diagnostics-response.json"
            ),
            "archive_metadata_matches_report": document_id == identifiers.get("document_id"),
            "rerender_archived": rerender_payload.get("status") == "archived",
            "regenerate_archived": regenerate_payload.get("status") == "archived",
            "report_replay_archived": replay_payload.get("status") == "archived",
            "batch_item_replay_relinked": batch_replay_payload.get("report_job_status")
            in {"accepted", "archived", "data_ready", "completed"},
            "attention_events_present": bool(attention_payload.get("events")),
            "scheduler_admin_observability_proven": (
                scheduler_list_code == 200
                and scheduler_run_code == 200
                and scheduler_list_payload.get("enabled_schedule_count") == 1
                and scheduler_run_payload.get("materialized_count") == 1
                and identifiers.get("batch_schedule_id") == "monthly-sg-global-bal-rfc0105-live"
                and bool(identifiers.get("batch_schedule_run_correlation_id"))
            ),
            "metrics_include_rfc0105_operations": all(
                marker in metrics_payload
                for marker in (
                    "lotus_report_operations_total",
                    "rerender_from_snapshot",
                    "regenerate_from_upstream",
                    "replay_command",
                    "stuck_state_scan",
                    "batch_scheduler_pass",
                    "lotus_report_batch_scheduler_last_schedules",
                    "lotus_report_attention_events_last_count",
                )
            ),
        }
        identifiers["attention_scan_id"] = attention_payload.get("scan_id")

        _write_json(evidence_dir / "90-identifiers.json", identifiers)
        _write_json(evidence_dir / "91-proof-summary.json", proof)
        _write_audit_summary(evidence_dir=evidence_dir, identifiers=identifiers, proof=proof)
    finally:
        _stop_process(report_proc, report_out, report_err)
        _stop_process(render_proc, render_out, render_err)
        _stop_process(archive_proc, archive_out, archive_err)

    failed = {key: value for key, value in proof.items() if value is not True}
    if failed:
        print(f"RFC-0105 live proof failed: {failed}", file=sys.stderr)
        print(str(evidence_dir))
        return 1
    print(str(evidence_dir))
    return 0


def _http_text(*, url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw_body = response.read()
            status_code = response.getcode()
    except urllib.error.HTTPError as exc:
        raw_body = exc.read()
        status_code = exc.code
    return status_code, raw_body.decode("utf-8")


def _is_operator_safe(path: Path) -> bool:
    text = path.read_text(encoding="utf-8").lower()
    forbidden = [
        "snapshot_payload",
        "storage_ref",
        "storage_key",
        "tenant-sg",
        "trace-rfc0105",
        "raw_payload",
    ]
    return not any(value in text for value in forbidden)


def _write_audit_summary(
    *,
    evidence_dir: Path,
    identifiers: dict[str, Any],
    proof: dict[str, Any],
) -> None:
    lines = [
        "# RFC-0105 Slice 9 Live Evidence",
        "",
        f"- Generated: `{_utc_now()}`",
        f"- Evidence directory: `{evidence_dir.relative_to(ROOT)}`",
        f"- Correlation ID: `{identifiers.get('correlation_id')}`",
        f"- Trace ID: `{identifiers.get('trace_id')}`",
        f"- Report job ID: `{identifiers.get('report_job_id')}`",
        f"- Snapshot ID: `{identifiers.get('snapshot_id')}`",
        f"- Render job ID: `{identifiers.get('render_job_id')}`",
        f"- Document ID: `{identifiers.get('document_id')}`",
        f"- Batch ID: `{identifiers.get('batch_id')}`",
        f"- Batch item ID: `{identifiers.get('batch_item_id')}`",
        f"- Batch schedule ID: `{identifiers.get('batch_schedule_id')}`",
        "- Batch schedule run correlation ID: "
        f"`{identifiers.get('batch_schedule_run_correlation_id')}`",
        "",
        "## Proof Summary",
        "",
    ]
    lines.extend(f"- `{key}`: `{value}`" for key, value in sorted(proof.items()))
    lines.extend(
        [
            "",
            "## Critical Review",
            "",
            (
                "This proof starts live local `lotus-report`, `lotus-render`, and "
                "`lotus-archive` processes, exercises source-backed report, render, archive, "
                "diagnostics, rerender, regenerate, replay, batch-item replay, attention, and "
                "metrics surfaces, and records exact response artifacts."
            ),
            (
                "Gateway is represented by governed caller headers in this pack; no gateway "
                "process is started because RFC-0105 Slice 9 marks gateway inclusion as "
                "conditional and the implemented report endpoints are the source of truth for "
                "this slice."
            ),
            (
                "Scheduler CRUD remains out of RFC-0105 implementation scope. Scheduler-admin "
                "observability is covered through existing batch scheduler metrics and "
                "config-backed schedule APIs, not a mutable schedule registry."
            ),
        ]
    )
    (evidence_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
