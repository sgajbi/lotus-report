"""Public review composition over unchanged HTTP clients and controlled owner facts.

These controls certify Report's projection, not upstream calculations or bank IAM.
"""

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from fastapi.testclient import TestClient

from app.clients.core_query_client import CoreQueryClient
from app.clients.performance_client import PerformanceClient
from app.clients.risk_client import RiskClient
from app.config import settings
from app.main import app
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_lineage.capture_service import PortfolioReviewSnapshotCaptureService
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.routers.reports import get_reporting_read_service
from app.services.reporting_read_service import ReportingReadService
from tests.integration.postgres_adapter_ownership import own_postgres_adapter

PORTFOLIO = "HISTORY_PORTFOLIO_001"
AS_OF = "2026-01-09"
CALCULATION_ID = "10000000-0000-4000-8000-000000000001"


def workspace_payload(status):
    start = "2026-01-05" if status == "partial" else "2026-01-01"
    observations = [
        date.fromisoformat(start) + timedelta(days=i)
        for i in range(5 if status == "partial" else 9)
    ]
    if status == "unknown":
        observations.remove(date(2026, 1, 6))
    block = {
        "summary": {
            "cumulative_return": {"base": 5.0},
            "annualized_return": {"base": 5.0},
        },
        "breakdowns": {
            "daily": [
                {
                    "period": day.isoformat(),
                    "period_start": day.isoformat(),
                    "period_end": day.isoformat(),
                    "period_return": {"base": 5.0 if day.isoformat() == AS_OF else 0.0},
                    "cumulative_return": {"base": 5.0 if day.isoformat() == AS_OF else 0.0},
                }
                for day in observations
            ]
        },
    }
    payload = {
        "portfolio_id": PORTFOLIO,
        "calculation_id": CALCULATION_ID,
        "input_mode": "stateful",
        "results_by_period": {
            "YTD": {
                "portfolio_twr": {"net": block, "gross": block},
                "benchmark": {
                    "benchmark_id": "HISTORY_BENCHMARK",
                    "summary": {"cumulative_return": {"base": 3.0}},
                },
                "active": {"net": {"cumulative_return": {"base": 2.0}}},
            }
        },
        "currency_evidence": {"applied_report_ccy": "USD"},
        "calculation_supportability": {
            "state": "ready" if status in {"complete", "missing"} else "degraded",
            "reason": "calculation_complete"
            if status in {"complete", "missing"}
            else f"{status}_history_coverage",
            "freshness_bucket": "current",
            "input_row_count": len(observations),
            "resolved_period_count": 6,
            "benchmark_row_count": len(observations),
        },
    }
    if status != "missing":
        missing = (
            ["2026-01-01", "2026-01-02"]
            if status == "partial"
            else ["2026-01-06"]
            if status == "unknown"
            else []
        )
        payload["calculation_supportability"]["history_coverage"] = {
            "status": status,
            "calculation_basis": "requested_window" if status == "complete" else "available_window",
            "requested_start_date": "2026-01-01",
            "requested_end_date": AS_OF,
            "covered_start_date": start,
            "covered_end_date": AS_OF,
            "effective_start_date": start,
            "effective_end_date": AS_OF,
            "calendar_basis": "business_weekdays",
            "missing_required_observation_count": len(missing),
            "missing_required_observation_dates_sample": missing,
            "reason_codes": ["leading_history_missing"]
            if status == "partial"
            else ["interior_history_missing", "venue_calendar_not_attested"]
            if status == "unknown"
            else ["covered_window_matches_requested_window"],
        }
    row = payload["results_by_period"]["YTD"]
    payload["results_by_period"] = {code: row for code in ("1M", "3M", "YTD", "1Y", "5Y", "SI")}
    return payload


@pytest.fixture
def source_http():
    state = {"workspace": None, "calls": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def log_message(self, *_args):
            pass

        def respond(self):
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size)) if size else None
            state["calls"].append(
                {
                    "path": self.path,
                    "body": body,
                    "tenant": self.headers.get("X-Tenant-Id"),
                    "correlation": self.headers.get("X-Correlation-ID"),
                    "trace": self.headers.get("X-Trace-ID"),
                }
            )
            status = 200
            if self.path == f"/portfolios/{PORTFOLIO}":
                payload = {
                    "portfolio_id": PORTFOLIO,
                    "client_id": "HISTORY_CLIENT",
                    "advisor_id": "HISTORY_ADVISOR",
                    "booking_center_code": "SG",
                    "portfolio_type": "advisory",
                    "objective": "capital_growth",
                    "risk_exposure": "balanced",
                    "investment_time_horizon": "long_term",
                }
            elif self.path == "/reporting/portfolio-summary/query":
                payload = {
                    "portfolio_id": PORTFOLIO,
                    "totals": {
                        "total_market_value_reporting_currency": 105.0,
                        "cash_balance_reporting_currency": 0.0,
                        "invested_market_value_reporting_currency": 105.0,
                    },
                    "snapshot_metadata": {"currency": "USD", "snapshot_date": AS_OF},
                }
            elif self.path == "/performance/workspace-summary":
                payload = state["workspace"]
            elif self.path == "/performance/contribution":
                payload = {
                    "results_by_period": {
                        "YTD": {"total_portfolio_return": 5.0, "total_contribution": 5.0}
                    }
                }
            elif self.path == "/analytics/risk/drawdown":
                payload = {"results": {}, "metadata": {}}
            else:
                status, payload = 503, {"code": "controlled_risk_unavailable"}
            content = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def public_review(source_http, payload, *, sections=None):
    base_url, state = source_http
    state["workspace"] = payload
    service = ReportingReadService(
        core_query_client=CoreQueryClient(base_url, 3, max_retries=0),
        performance_client=PerformanceClient(base_url, 3, max_retries=0),
        risk_client=RiskClient(base_url, 3, max_retries=0),
    )
    app.dependency_overrides[get_reporting_read_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/reports/portfolios/{PORTFOLIO}/review",
                headers={
                    "X-Tenant-Id": "history-owner",
                    "X-Correlation-ID": "history-correlation",
                    "X-Trace-ID": "history-trace",
                },
                json={
                    "as_of_date": AS_OF,
                    "reporting_currency": "USD",
                    "sections": sections or ["PERFORMANCE"],
                    "benchmark_code": "HISTORY_BENCHMARK",
                },
            )
    finally:
        app.dependency_overrides.pop(get_reporting_read_service, None)
    assert response.status_code == 200, response.text
    assert all(call["tenant"] == "history-owner" for call in state["calls"])
    assert all(call["correlation"] == "history-correlation" for call in state["calls"])
    assert all(call["trace"] == "history-trace" for call in state["calls"])
    return response.json()


@pytest.mark.parametrize("status", ["complete", "partial", "unknown", "missing"])
def test_public_review_preserves_history_and_qualified_readiness(source_http, status):
    payload = workspace_payload(status)
    body = public_review(source_http, payload)
    performance = body["performance"]
    assert performance["summary"]["YTD"]["net_cumulative_return"] == 5.0
    expected = "ready" if status == "complete" else "partial"
    assert performance["supportability"]["status"] == expected
    assert body["readiness"]["status"] == expected
    assert body["audience"]["client_ready"] is (status == "complete")
    qualification = performance["history_qualification"]
    assert qualification["status"] == status
    assert qualification["source_calculation_id"] == CALCULATION_ID
    assert qualification["source_portfolio_id"] == PORTFOLIO
    if status != "missing":
        assert (
            qualification["coverage"] == payload["calculation_supportability"]["history_coverage"]
        )
    trust = body["evidence"]["trust_metadata"]
    assert trust["completeness_status"] == ("complete" if status == "complete" else "partial")
    assert trust["data_quality_status"] == (
        "quality_passed" if status == "complete" else "quality_warning"
    )
    disclosure = next(
        d for d in body["disclosures"] if d["disclosure_id"] == "performance_history_qualification"
    )
    assert f"qualification: {status}" in disclosure["text"]


@pytest.mark.parametrize(
    "case",
    [
        "wrong_end",
        "bad_calculation_id",
        "wrong_input_mode",
        "malformed",
        "contradictory_complete",
        "unknown_reason",
        "mixed_basis",
        "degraded_complete",
        "stale_complete",
    ],
)
def test_public_review_refuses_unqualified_source_promotion(source_http, case):
    import copy

    payload = workspace_payload("complete")
    support = payload["calculation_supportability"]
    coverage = support["history_coverage"]
    expected_status = "mismatched"
    if case == "wrong_portfolio":
        payload["portfolio_id"] = "OTHER_PORTFOLIO"
    elif case == "wrong_end":
        coverage["requested_end_date"] = "2026-01-10"
    elif case == "bad_calculation_id":
        payload["calculation_id"] = "invalid"
        expected_status = "invalid"
    elif case == "wrong_input_mode":
        payload["input_mode"] = "stateless"
    elif case == "malformed":
        support["history_coverage"] = "not-an-object"
        expected_status = "invalid"
    elif case == "contradictory_complete":
        coverage["missing_required_observation_count"] = 1
        expected_status = "invalid"
    elif case == "unknown_reason":
        coverage["reason_codes"] = ["unsafe arbitrary source narrative"]
        expected_status = "invalid"
    elif case == "mixed_basis":
        payload["results_by_period"]["1Y"] = copy.deepcopy(payload["results_by_period"]["YTD"])
        payload["results_by_period"]["1Y"]["portfolio_twr"].pop("net")
    elif case == "degraded_complete":
        support.update(state="degraded", reason="calculation_quality_issue")
        expected_status = "complete"
    else:
        support.update(state="stale", reason="stale_source_observations", freshness_bucket="stale")
        expected_status = "complete"
    body = public_review(source_http, payload)
    assert body["performance"]["summary"]["YTD"]["net_cumulative_return"] == 5.0
    assert body["performance"]["history_qualification"]["status"] == expected_status
    assert body["performance"]["history_qualification"]["client_publication_allowed"] is False
    assert body["performance"]["supportability"]["status"] == "partial"
    assert body["readiness"]["status"] == "partial"
    assert body["audience"]["client_ready"] is False


_REREAD_CAPTURE = """
import json, os, sys
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from app.reporting_render.package_builder import _build_render_package
backend, job_path, snapshot_path, job_id = sys.argv[1:]
if backend == 'postgres':
    ledger = PostgresReportJobLedger(os.environ['REPORT_JOB_LEDGER_DATABASE_URL'])
    store = PostgresReportInputSnapshotStore(os.environ['REPORT_JOB_LEDGER_DATABASE_URL'])
else:
    ledger = ReportJobLedger(job_path)
    store = ReportInputSnapshotStore(snapshot_path)
try:
    snapshot = store.get_snapshot_by_job(job_id)
    package = _build_render_package(job=ledger.get_job(job_id),
        snapshot=snapshot.snapshot_payload, render_job_id='history-retained-render',
        snapshot_id=snapshot.snapshot_id)
    print(json.dumps({'hash': snapshot.snapshot_hash, 'payload': snapshot.snapshot_payload,
        'supportability': snapshot.supportability_status, 'package': package}))
finally:
    if backend == 'postgres':
        store.close()
        ledger.close()
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("status", ["complete", "partial", "unknown", "missing"])
async def test_native_capture_survives_process_reread_with_original_history(
    source_http, tmp_path, monkeypatch, backend, status
):
    base_url, state = source_http
    state["workspace"] = workspace_payload(status)
    for key in ("core_query_base_url", "performance_base_url", "risk_base_url"):
        monkeypatch.setattr(settings, key, base_url)
    monkeypatch.setattr(settings, "upstream_max_retries", 0)
    job_path, snapshot_path = tmp_path / "jobs.sqlite3", tmp_path / "snapshots.sqlite3"
    if backend == "postgres":
        database_url = os.getenv("REPORT_JOB_LEDGER_DATABASE_URL")
        if not database_url:
            pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL required for PostgreSQL history proof")
        ledger = own_postgres_adapter(PostgresReportJobLedger(database_url))
        store = own_postgres_adapter(PostgresReportInputSnapshotStore(database_url))
    else:
        ledger, store = ReportJobLedger(job_path), ReportInputSnapshotStore(snapshot_path)
    caller = ReportCallerContext(
        triggered_by="history-advisor",
        caller_application="history-proof",
        tenant_id="history-owner",
        region="APAC",
        booking_center_code="SG",
        correlation_id="history-capture-corr",
        trace_id="history-capture-trace",
    )
    request = PortfolioReviewJobRequest(
        portfolio_scope={"portfolio_ids": [PORTFOLIO]},
        as_of_date=AS_OF,
        requested_output_formats=["pdf"],
        reporting_currency="USD",
        options={"sections": ["PERFORMANCE"]},
    )
    job = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller,
        idempotency_key=f"history-{backend}-{status}-{tmp_path.name}",
    )
    capture = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)
    completed = await capture.capture_for_job(job)
    assert completed.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    expected = "complete" if status == "complete" else "partial"
    assert snapshot.supportability_status == expected
    assert snapshot.completeness_status == expected
    calls = store.list_upstream_calls(snapshot.snapshot_id)
    workspace = next(call for call in calls if call.endpoint == "/performance/workspace-summary")
    assert workspace.supportability_status == expected
    assert workspace.completeness_status == expected
    assert snapshot.snapshot_payload["performance"]["history_qualification"]["status"] == status
    source_count = len(state["calls"])
    state["workspace"] = workspace_payload("complete")
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            _REREAD_CAPTURE,
            backend,
            str(job_path),
            str(snapshot_path),
            job.job_id,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    retained = json.loads(child.stdout)
    assert retained["hash"] == snapshot.snapshot_hash
    assert retained["payload"] == snapshot.snapshot_payload
    assert retained["supportability"] == expected
    assert len(state["calls"]) == source_count
    data = retained["package"]["report_data"]
    assert data["performance_history_qualification"]["status"] == status
    assert data["governance_summary"]["readiness_status"] == (
        "ready" if status == "complete" else "partial"
    )
    assert f"qualification: {status}" in data["review_observations"][-1]
    assert all(call["tenant"] == "history-owner" for call in state["calls"])
    receipts_directory = os.getenv("REPORT_HISTORY_PACKAGE_RECEIPTS_DIR")
    if receipts_directory:
        target = Path(receipts_directory)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{backend}-{status}.json").write_text(
            json.dumps(retained, indent=2, default=str), encoding="utf-8"
        )


def test_public_review_refuses_foreign_source_figures_before_projection(source_http):
    payload = workspace_payload("complete")
    payload["portfolio_id"] = "OTHER_PORTFOLIO"
    body = public_review(source_http, payload)
    assert body["performance"] is None
    assert body["audience"]["client_ready"] is False
    assert not any(call["path"] == "/performance/contribution" for call in source_http[1]["calls"])


@pytest.mark.parametrize("case", ["annualized_boolean", "gross_string"])
def test_public_review_never_presents_malformed_twr_readings(source_http, case):
    import copy

    payload = workspace_payload("complete")
    row = copy.deepcopy(payload["results_by_period"]["1Y"])
    row["portfolio_twr"]["gross"] = copy.deepcopy(row["portfolio_twr"]["gross"])
    basis, key, value = (
        ("net", "annualized_return", True)
        if case == "annualized_boolean"
        else ("gross", "cumulative_return", "7.25")
    )
    row["portfolio_twr"][basis]["summary"][key]["base"] = value
    payload["results_by_period"]["1Y"] = row
    body = public_review(source_http, payload)
    assert body["performance"]["summary"]["1Y"][f"{basis}_{key}"] is None
    assert body["performance"]["summary"]["1Y"]["net_cumulative_return"] == 5.0
    assert body["performance"]["history_qualification"]["client_publication_allowed"] is False
    assert body["performance"]["history_qualification"]["status"] == "mismatched"
    assert body["readiness"]["status"] == "partial"
    assert body["audience"]["client_ready"] is False


@pytest.mark.parametrize("sections", [["RISK_ANALYTICS"], ["PERFORMANCE", "RISK_ANALYTICS"]])
@pytest.mark.parametrize("identity", ["owner", "foreign", "missing"])
def test_risk_workspace_fetch_requires_matching_portfolio(source_http, sections, identity):
    payload = workspace_payload("complete")
    if identity == "foreign":
        payload["portfolio_id"] = "FOREIGN_HISTORY_PORTFOLIO"
    elif identity == "missing":
        payload.pop("portfolio_id")
    body = public_review(source_http, payload, sections=sections)
    calculate_calls = [
        call for call in source_http[1]["calls"] if call["path"] == "/analytics/risk/calculate"
    ]
    if identity == "owner":
        assert calculate_calls
        assert all(
            call["body"]["stateful_input"]["portfolio_id"] == PORTFOLIO for call in calculate_calls
        )
    else:
        assert calculate_calls == []
        support = body["risk_analytics"]["supportability"]
        assert support["status"] == "unavailable"
        assert support["notes"][0]["code"] == "risk_return_history_unavailable"
        assert body["audience"]["client_ready"] is False


@pytest.mark.parametrize(
    "reason,missing",
    [
        ("interior_history_missing", "2026-01-06"),
        ("trailing_history_missing", "2026-01-09"),
    ],
)
@pytest.mark.parametrize("value", [0.0, -5.0])
def test_partial_gaps_preserve_zero_negative_and_benchmark_values(
    source_http, reason, missing, value
):
    payload = workspace_payload("complete")
    support = payload["calculation_supportability"]
    support.update(state="degraded", reason="partial_history_coverage")
    coverage = support["history_coverage"]
    coverage.update(
        status="partial",
        calculation_basis="available_window",
        missing_required_observation_count=1,
        missing_required_observation_dates_sample=[missing],
        reason_codes=[reason],
    )
    if reason == "trailing_history_missing":
        coverage.update(covered_end_date="2026-01-08", effective_end_date="2026-01-08")
    row = payload["results_by_period"]["YTD"]
    for block in row["portfolio_twr"].values():
        block["summary"]["cumulative_return"]["base"] = value
        block["breakdowns"]["daily"] = [
            day for day in block["breakdowns"]["daily"] if day["period"] != missing
        ]
    body = public_review(source_http, payload)
    summary = body["performance"]["summary"]["YTD"]
    assert summary["net_cumulative_return"] == value
    assert summary["benchmark_cumulative_return"] == 3.0
    assert body["performance"]["history_qualification"]["coverage"] == coverage
    assert body["audience"]["client_ready"] is False
