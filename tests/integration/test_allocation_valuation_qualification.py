"""Native Report HTTP and durable capture over controlled Core HTTP evidence."""

import copy
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import httpx
import pytest
import uvicorn

from app.clients.core_query_client import CoreQueryClient
from app.config import settings
from app.main import app
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_lineage.capture_service import PortfolioReviewSnapshotCaptureService
from app.reporting_lineage.models import ReportInputSnapshotCreateRequest
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.routers.reports import get_reporting_read_service
from app.services.reporting_read_service import ReportingReadService
from tests.integration.postgres_adapter_ownership import own_postgres_adapter

PORTFOLIO = "ALLOCATION_QUALIFICATION_001"
AS_OF = "2026-04-10"
TENANT = "allocation-owner"


def allocation_payload(case):
    amounts, weights = (80, 20), (0.8, 0.2)
    state, reason = "COMPLETE", "all_source_positions_covered"
    if case in {"signed", "carry", "look_through"}:
        amounts, weights = (120, -20), (1.2, -0.2)
    if case == "zero":
        amounts, weights = (0, 0), (0, 0)
        state, reason = "MEASURED_ZERO", "source_measured_zero"
    elif case == "partial":
        amounts, weights = (120, None), (None, None)
        state, reason = "PARTIAL", "market_value_missing"
    elif case == "carry":
        state, reason = "CARRY_FORWARD", "latest_source_snapshot_precedes_as_of_date"
    elif case == "empty":
        amounts, weights = (), ()
        state, reason = "LOADED_EMPTY", "source_snapshot_has_no_open_positions"
    elif case == "unavailable":
        amounts, weights = (), ()
        state, reason = "UNAVAILABLE", "no_source_snapshot"
    total = None if case in {"partial", "unavailable"} else sum(amounts)
    source = {
        "scope_type": "portfolio",
        "scope": {"portfolio_id": PORTFOLIO},
        "resolved_as_of_date": AS_OF,
        "reporting_currency": "USD",
        "total_market_value_reporting_currency": total,
        "valuation_coverage": {
            "coverage_state": state,
            "coverage_reason": reason,
            "snapshot_row_count": len(amounts),
            "expected_open_position_count": len(amounts),
            "valued_position_count": len(amounts) - (case == "partial"),
            "unvalued_position_count": int(case == "partial"),
        },
        "look_through": {
            "requested_mode": "prefer_look_through",
            "supported": case == "look_through",
            "applied_mode": "look_through" if case == "look_through" else "direct_only",
        },
        "calculation_lineage": {
            "policy_version": "core-allocation-v1",
            "output_hash": "sha256:source",
        },
        "views": [
            {
                "dimension": "asset_class",
                "total_market_value_reporting_currency": total,
                "buckets": [
                    {
                        "dimension_value": label,
                        "weight": weight,
                        "market_value_reporting_currency": amount,
                        "position_count": 1,
                    }
                    for label, amount, weight in zip(("Equity", "Cash"), amounts, weights)
                ],
            }
        ],
    }
    return source


@pytest.fixture
def source_http():
    state = {"allocation": None, "calls": []}

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
                }
            )
            status = 200
            if self.headers.get("X-Tenant-Id") != TENANT:
                status, payload = 404, {"detail": "portfolio_not_found"}
            elif self.path == "/reporting/asset-allocation/query":
                payload = state["allocation"]
            elif self.path == "/reporting/portfolio-summary/query":
                payload = {
                    "portfolio_id": PORTFOLIO,
                    "reporting_currency": "USD",
                    "totals": {},
                    "snapshot_metadata": {"snapshot_date": AS_OF},
                }
            elif self.path == f"/portfolios/{PORTFOLIO}":
                payload = {
                    "portfolio_id": PORTFOLIO,
                    "client_id": "ALLOCATION_CLIENT",
                    "advisor_id": "ALLOCATION_ADVISOR",
                    "booking_center_code": "SG",
                    "portfolio_type": "advisory",
                    "objective": "capital_growth",
                    "risk_exposure": "balanced",
                    "investment_time_horizon": "long_term",
                }
            else:
                status, payload = 503, {"code": "controlled_other_service_unavailable"}
            content = json.dumps(payload, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.fixture
def report_http(source_http):
    url, _ = source_http
    service = ReportingReadService(core_query_client=CoreQueryClient(url, 3, max_retries=0))
    app.dependency_overrides[get_reporting_read_service] = lambda: service
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]})
    thread.start()
    try:
        deadline = monotonic() + 10
        while not server.started and thread.is_alive() and monotonic() < deadline:
            sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        app.dependency_overrides.pop(get_reporting_read_service, None)
        assert not thread.is_alive()


@pytest.mark.parametrize(
    "case",
    ["complete", "signed", "zero", "partial", "carry", "empty", "unavailable", "look_through"],
)
def test_registered_native_summary_and_review_preserve_qualified_numbers(
    source_http, report_http, case
):
    _, state = source_http
    source = allocation_payload(case)
    state["allocation"] = source
    bodies = {}
    for route in ("summary", "review"):
        response = httpx.post(
            f"{report_http}/reports/portfolios/{PORTFOLIO}/{route}",
            headers={"X-Tenant-Id": TENANT, "X-Correlation-ID": "allocation-http"},
            json={"as_of_date": AS_OF, "reporting_currency": "USD", "sections": ["ALLOCATION"]},
            timeout=10,
        )
        assert response.status_code == 200, response.text
        bodies[route] = response.json()
        rows = bodies[route]["allocation"]["byAssetClass"]
        assert [(row["market_value"], row["weight"]) for row in rows] == [
            (row["market_value_reporting_currency"], row["weight"])
            for row in source["views"][0]["buckets"]
        ]
        assert bodies[route]["allocation"]["valuation_coverage"] == source["valuation_coverage"]
    assert bodies["review"]["allocation"]["qualification"]["client_publication_allowed"] is (
        case not in {"partial", "carry", "unavailable"}
    )
    section = next(
        row
        for row in bodies["review"]["client_sections"]
        if row["section_id"] == "asset_allocation"
    )
    assert section["status"] == (
        "partial" if case in {"partial", "carry", "unavailable"} else "ready"
    )
    if case == "partial":
        assert bodies["review"]["key_figures"]["allocation"]["largest_asset_class"] is None
        assert section["items"][1]["market_value"] is None
    assert all(call["tenant"] == TENANT for call in state["calls"])
    if evidence := os.getenv("REPORT_ALLOCATION_RECEIPTS_DIR"):
        target = Path(evidence)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"http-{case}.json").write_text(
            json.dumps(
                {
                    "source": source,
                    "bodies": bodies,
                    "calls": state["calls"],
                    "public_url": report_http,
                    "source_url": source_http[0],
                },
                indent=2,
            ),
            encoding="utf-8",
        )


@pytest.mark.parametrize(
    "change", ["foreign", "date", "currency", "counts", "view_empty", "bucket_count", "empty_rows"]
)
def test_public_source_refusal_and_bounded_invalid_qualification(source_http, report_http, change):
    _, state = source_http
    state["allocation"] = allocation_payload("complete")
    if change == "foreign":
        state["allocation"]["scope"]["portfolio_id"] = "FOREIGN"
    elif change == "date":
        state["allocation"]["resolved_as_of_date"] = "2026-04-09"
    elif change == "currency":
        state["allocation"]["reporting_currency"] = "EUR"
    elif change == "counts":
        state["allocation"]["valuation_coverage"]["unvalued_position_count"] = 1
    elif change == "view_empty":
        state["allocation"]["views"].append(
            {"dimension": "currency", "buckets": [], "total_market_value_reporting_currency": 100}
        )
    elif change == "bucket_count":
        state["allocation"]["views"][0]["buckets"][0]["position_count"] = True
    else:
        state["allocation"] = allocation_payload("empty")
        state["allocation"]["views"][0]["buckets"] = [
            {
                "dimension_value": "Cash",
                "weight": 0,
                "market_value_reporting_currency": 0,
                "position_count": 0,
            }
        ]
    response = httpx.post(
        f"{report_http}/reports/portfolios/{PORTFOLIO}/review",
        headers={"X-Tenant-Id": TENANT},
        json={"as_of_date": AS_OF, "sections": ["ALLOCATION"]},
        timeout=10,
    )
    assert response.status_code == (502 if change in {"foreign", "date", "currency"} else 200)
    if change not in {"foreign", "date", "currency"}:
        assert response.json()["allocation"]["qualification"]["status"] == "invalid"
        assert response.json()["audience"]["client_ready"] is False
    else:
        assert "byAssetClass" not in response.text


def test_missing_and_foreign_summary_caller_cannot_obtain_financial_figures(
    source_http, report_http
):
    _, state = source_http
    state["allocation"] = allocation_payload("signed")
    url = f"{report_http}/reports/portfolios/{PORTFOLIO}/summary"
    for tenant in (None, ""):
        response = httpx.post(
            url,
            headers={} if tenant is None else {"X-Tenant-Id": tenant},
            json={"as_of_date": AS_OF, "sections": ["ALLOCATION"]},
            timeout=10,
        )
        assert response.status_code == 400
        assert not state["calls"]
    response = httpx.post(
        url,
        headers={"X-Tenant-Id": "foreign-owner"},
        json={"as_of_date": AS_OF, "sections": ["ALLOCATION"]},
        timeout=10,
    )
    assert response.status_code == 404 and "byAssetClass" not in response.text


REREAD = """
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
    package = _build_render_package(job=ledger.get_job(job_id), snapshot=snapshot.snapshot_payload,
        render_job_id='allocation-retained-render', snapshot_id=snapshot.snapshot_id)
    print(json.dumps({'hash': snapshot.snapshot_hash, 'payload': snapshot.snapshot_payload,
                     'supportability': snapshot.supportability_status, 'package': package}))
finally:
    if backend == 'postgres':
        store.close()
        ledger.close()
"""


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("case", ["signed", "zero", "partial", "carry", "empty", "unavailable"])
async def test_durable_capture_and_process_reread_preserve_unknown_and_original_hash(
    source_http, tmp_path, monkeypatch, backend, case
):
    url, state = source_http
    state["allocation"] = allocation_payload(case)
    monkeypatch.setattr(settings, "core_query_base_url", url)
    monkeypatch.setattr(settings, "upstream_max_retries", 0)
    job_path, snapshot_path = tmp_path / "jobs.sqlite3", tmp_path / "snapshots.sqlite3"
    if backend == "postgres":
        database_url = os.getenv("REPORT_JOB_LEDGER_DATABASE_URL")
        if not database_url:
            pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL required for PostgreSQL allocation proof")
        ledger = own_postgres_adapter(PostgresReportJobLedger(database_url))
        store = own_postgres_adapter(PostgresReportInputSnapshotStore(database_url))
    else:
        ledger, store = ReportJobLedger(job_path), ReportInputSnapshotStore(snapshot_path)
    caller = ReportCallerContext(
        triggered_by="allocation-advisor",
        caller_application="allocation-proof",
        tenant_id=TENANT,
        region="APAC",
        booking_center_code="SG",
        correlation_id="allocation-capture",
        trace_id="allocation-trace",
    )
    request = PortfolioReviewJobRequest(
        portfolio_scope={"portfolio_ids": [PORTFOLIO]},
        as_of_date=AS_OF,
        reporting_currency="USD",
        requested_output_formats=["pdf"],
        options={"sections": ["ALLOCATION"]},
    )
    job = ledger.create_portfolio_review_job(
        request=request,
        caller_context=caller,
        idempotency_key=f"allocation-{case}-{backend}-{tmp_path.name}",
    )
    capture = PortfolioReviewSnapshotCaptureService(snapshot_store=store, job_ledger=ledger)
    completed = await capture.capture_for_job(job)
    assert completed.status == "data_ready"
    snapshot = store.get_snapshot_by_job(job.job_id)
    allocation_call = next(
        row
        for row in store.list_upstream_calls(snapshot.snapshot_id)
        if row.endpoint == "/reporting/asset-allocation/query"
    )
    expected = "partial" if case in {"partial", "carry", "unavailable"} else "complete"
    assert allocation_call.supportability_status == expected
    assert allocation_call.completeness_status == expected
    original = copy.deepcopy(snapshot.snapshot_payload)
    source_count = len(state["calls"])
    state["allocation"] = allocation_payload("complete")
    child = subprocess.run(
        [sys.executable, "-c", REREAD, backend, str(job_path), str(snapshot_path), job.job_id],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    retained = json.loads(child.stdout)
    assert retained["hash"] == snapshot.snapshot_hash and retained["payload"] == original
    assert len(state["calls"]) == source_count
    data = retained["package"]["report_data"]
    assert data["allocation_valuation_qualification"] == original["allocation"]["qualification"]
    rows = data["allocation_breakdowns"]["by_asset_class"]
    if case == "signed":
        assert [(row["weight_pct"], row["market_value"]) for row in rows] == [
            ("120.00%", "120.00"),
            ("-20.00%", "-20.00"),
        ]
    elif case == "zero":
        assert all(row["weight_pct"] == "0.00%" and row["market_value"] == "0.00" for row in rows)
    elif case == "partial":
        assert rows[0]["weight_pct"] == "Not available" and rows[0]["market_value"] == "120.00"
        assert rows[1]["weight_pct"] == rows[1]["market_value"] == "Not available"
        assert data["governance_summary"]["readiness_status"] == "partial"
    else:
        assert len(rows) == (2 if case == "carry" else 0)
    posture = data["allocation_presentation"]["dimensions"][0]["posture"]
    assert posture == (
        "empty" if case == "empty" else "unavailable" if case == "unavailable" else "ready"
    )
    if evidence := os.getenv("REPORT_ALLOCATION_RECEIPTS_DIR"):
        target = Path(evidence)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{backend}-{case}.json").write_text(
            json.dumps(retained, indent=2), encoding="utf-8"
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_retained_legacy_capture_with_zero_has_no_rewrite_or_source_refresh(
    source_http, tmp_path, backend
):
    _, state = source_http
    job_path, snapshot_path = tmp_path / "jobs.sqlite3", tmp_path / "snapshots.sqlite3"
    if backend == "postgres":
        database_url = os.getenv("REPORT_JOB_LEDGER_DATABASE_URL")
        if not database_url:
            pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL required for legacy PostgreSQL proof")
        ledger = own_postgres_adapter(PostgresReportJobLedger(database_url))
        store = own_postgres_adapter(PostgresReportInputSnapshotStore(database_url))
    else:
        ledger, store = ReportJobLedger(job_path), ReportInputSnapshotStore(snapshot_path)
    request = PortfolioReviewJobRequest(
        portfolio_scope={"portfolio_ids": [PORTFOLIO]},
        as_of_date=AS_OF,
        reporting_currency="USD",
        requested_output_formats=["pdf"],
        options={"sections": ["ALLOCATION"]},
    )
    caller = ReportCallerContext(
        triggered_by="allocation-advisor",
        caller_application="allocation-proof",
        tenant_id=TENANT,
        region="APAC",
        booking_center_code="SG",
        correlation_id="legacy-allocation",
        trace_id="legacy-trace",
    )
    job = ledger.create_portfolio_review_job(
        request=request, caller_context=caller, idempotency_key=f"legacy-{backend}-{tmp_path.name}"
    )
    payload = {
        "portfolio_id": PORTFOLIO,
        "as_of_date": AS_OF,
        "reportingCurrency": "USD",
        "readiness": {"status": "ready"},
        "evidence": {
            "trust_metadata": {
                "completeness_status": "complete",
                "data_quality_status": "quality_passed",
            }
        },
        "allocation": {
            "byAssetClass": [
                {"group": "Equity", "weight": 1.0, "market_value": 120, "position_count": 1},
                {"group": "Cash", "weight": 0.0, "market_value": 0.0, "position_count": 1},
            ]
        },
        "allocation_presentation": {
            "resolved_by": "caller_request",
            "dimensions": [
                {"dimension": "asset_class", "package_key": "by_asset_class", "posture": "ready"}
            ],
        },
    }
    snapshot = store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type="portfolio_review",
            report_data_contract_version="v1",
            portfolio_scope={"portfolio_ids": [PORTFOLIO]},
            as_of_date=AS_OF,
            snapshot_payload=payload,
            supportability_status="complete",
            completeness_status="complete",
            captured_at=datetime.now(UTC),
            correlation_id="legacy-allocation",
            trace_id="legacy-trace",
            tenant_id=TENANT,
            region="APAC",
            booking_center_code="SG",
        )
    )
    child = subprocess.run(
        [sys.executable, "-c", REREAD, backend, str(job_path), str(snapshot_path), job.job_id],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    retained = json.loads(child.stdout)
    assert retained["hash"] == snapshot.snapshot_hash and retained["payload"] == payload
    assert not state["calls"]
    data = retained["package"]["report_data"]
    assert data["allocation_valuation_qualification"]["status"] == "missing"
    assert data["governance_summary"]["readiness_status"] == "partial"
    assert data["governance_summary"]["completeness_status"] == "partial"
    rows = data["allocation_breakdowns"]["by_asset_class"]
    assert [row["name"] for row in rows] == ["Equity", "Cash"]
    assert all(row["market_value"] == row["weight_pct"] == "Not available" for row in rows)
    if evidence := os.getenv("REPORT_ALLOCATION_RECEIPTS_DIR"):
        target = Path(evidence)
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{backend}-legacy.json").write_text(
            json.dumps(retained, indent=2), encoding="utf-8"
        )
