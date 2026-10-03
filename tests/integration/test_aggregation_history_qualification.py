"""Registered Report HTTP and unchanged PerformanceClient over controlled source HTTP."""

import copy
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

from app.clients.performance_client import PerformanceClient
from app.main import app
from app.observability import propagation_headers
from app.services.aggregation_service import AggregationService

PORTFOLIO = "AGGREGATION_HISTORY_OWNER"
AS_OF = "2026-01-09"
TENANT = "aggregation-history-tenant"
CORRELATION = "aggregation-history-correlation"
TRACE = "0123456789abcdef0123456789abcdef"
SOURCE_REQUEST = {
    "portfolio_id": PORTFOLIO,
    "report_end_date": AS_OF,
    "input_mode": "stateful",
    "stateful_input": {},
    "periods": [{"period": "YTD", "frequencies": ["daily"]}],
}


def _workspace(status="complete", value=5.0):
    payload = {
        "portfolio_id": PORTFOLIO,
        "calculation_id": "10000000-0000-4000-8000-000000000001",
        "input_mode": "stateful",
        "results_by_period": {
            "YTD": {"portfolio_twr": {"net": {"summary": {"cumulative_return": {"base": value}}}}}
        },
        "calculation_supportability": {
            "state": "ready" if status in {"complete", "missing"} else "degraded",
            "reason": "calculation_complete"
            if status in {"complete", "missing"}
            else f"{status}_history_coverage",
            "freshness_bucket": "current",
        },
    }
    if status != "missing":
        missing = ["2026-01-01", "2026-01-02"] if status == "partial" else []
        if status == "unknown":
            missing = ["2026-01-06"]
        start = "2026-01-05" if status == "partial" else "2026-01-01"
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
    return payload


class _Core:
    def __init__(self, state):
        self.state = state

    def _record(self, portfolio_id, payload, admitted_tenant_id, endpoint):
        assert portfolio_id == PORTFOLIO and admitted_tenant_id == TENANT
        headers = propagation_headers()
        assert headers["X-Correlation-Id"] == CORRELATION and headers["X-Trace-Id"] == TRACE
        self.state["core_calls"].append({"endpoint": endpoint, "payload": payload})

    async def get_portfolio_summary(self, portfolio_id, payload, *, admitted_tenant_id):
        self._record(portfolio_id, payload, admitted_tenant_id, "summary")
        assert payload == {"as_of_date": AS_OF}
        return 200, {
            "reporting_currency": "USD",
            "totals": {"total_market_value_reporting_currency": 100},
            "snapshot_metadata": {"position_count": 1},
        }

    async def get_asset_allocation(self, portfolio_id, payload, *, admitted_tenant_id):
        self._record(portfolio_id, payload, admitted_tenant_id, "allocation")
        assert payload == {"as_of_date": AS_OF, "dimensions": ["asset_class"]}
        return 200, {
            "reporting_currency": "USD",
            "total_market_value_reporting_currency": 100,
            "views": [
                {
                    "dimension": "asset_class",
                    "buckets": [
                        {
                            "dimension_value": "Equity",
                            "market_value_reporting_currency": 100,
                            "weight": 1,
                        }
                    ],
                }
            ],
        }


@pytest.fixture(scope="module")
def native_http_world():
    state = {"payload": None, "status": 200, "source_calls": [], "core_calls": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["source_calls"].append(
                {
                    "path": self.path,
                    "body": request,
                    "tenant": self.headers.get("X-Tenant-Id"),
                    "correlation": self.headers.get("X-Correlation-Id"),
                    "trace": self.headers.get("X-Trace-Id"),
                    "traceparent": self.headers.get("traceparent"),
                }
            )
            content = json.dumps(state["payload"], allow_nan=False).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
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
        with httpx.Client() as client:
            yield {
                "state": state,
                "client": client,
                "source_url": f"http://127.0.0.1:{source.server_port}",
                "public_url": f"http://127.0.0.1:{report_port}/aggregations/portfolios/{PORTFOLIO}?as_of_date={AS_OF}",
            }
    finally:
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
        if capture := os.getenv("REPORT_AGGREGATION_HISTORY_EVIDENCE_DIR"):
            target = Path(capture)
            target.mkdir(parents=True, exist_ok=True)
            (target / "listeners-closed.json").write_text(
                json.dumps({"report_listener_closed": True, "source_listener_closed": True}),
                encoding="utf-8",
            )


def _public(monkeypatch, world, payload, *, status=200):
    before = copy.deepcopy(payload)
    state = world["state"]
    state.update(payload=payload, status=status, source_calls=[], core_calls=[])
    service = AggregationService(
        core_query_client=_Core(state),
        performance_client=PerformanceClient(world["source_url"], 3, max_retries=0),
    )
    monkeypatch.setattr("app.routers.aggregations.AggregationService", lambda: service)
    response = world["client"].get(
        world["public_url"],
        headers={
            "X-Tenant-Id": TENANT,
            "X-Correlation-Id": CORRELATION,
            "X-Trace-Id": TRACE,
        },
        timeout=10,
    )
    assert response.status_code == 200, response.text
    assert response.headers["X-Correlation-Id"] == CORRELATION
    assert response.headers["X-Trace-Id"] == TRACE
    assert len(state["source_calls"]) == 1 and len(state["core_calls"]) == 2
    call = state["source_calls"][0]
    assert call["path"] == "/performance/workspace-summary" and call["body"] == SOURCE_REQUEST
    assert (
        call["tenant"] == TENANT and call["correlation"] == CORRELATION and call["trace"] == TRACE
    )
    assert call["traceparent"].startswith(f"00-{TRACE}-")
    assert payload == before
    body = response.json()
    json.dumps(body, allow_nan=False)
    assert body["allocation_supportability"]["status"] == "available"
    assert "untrusted-source-detail" not in response.text
    if capture := os.getenv("REPORT_AGGREGATION_HISTORY_EVIDENCE_DIR"):
        target = Path(capture)
        target.mkdir(parents=True, exist_ok=True)
        record = {
            "source_status": status,
            "source_payload": before,
            "source_calls": state["source_calls"],
            "core_calls": state["core_calls"],
            "response_status": response.status_code,
            "response": body,
            "public_url": world["public_url"],
            "source_url": world["source_url"],
        }
        (target / f"case-{len(list(target.glob('case-*.json'))):03}.json").write_text(
            json.dumps(record, indent=2, allow_nan=False),
            encoding="utf-8",
        )
    return body


@pytest.mark.parametrize("status", ["complete", "partial", "unknown", "missing"])
@pytest.mark.parametrize("value", [0.0, -2.75, 5.0])
def test_valid_available_return_preserves_source_owned_history(
    monkeypatch, native_http_world, status, value
):
    payload = _workspace(status, value)
    body = _public(monkeypatch, native_http_world, payload)
    assert [row["value"] for row in body["rows"] if row["metric"] == "return_ytd_pct"] == [value]
    assert body["unavailable_sources"] == []
    history = body["performance_history_qualification"]
    assert history["status"] == status
    assert history["client_publication_allowed"] is (status == "complete")
    assert history["source_portfolio_id"] == PORTFOLIO
    assert history["source_calculation_id"] == payload["calculation_id"]
    assert history["requested_periods"] == history["returned_periods"] == ["YTD"]
    assert history["period_return_bases"] == {"YTD": ["NET_TWR"]}
    assert history["coverage_scope"] == "calculation_union_window"
    assert history["coverage"] == payload["calculation_supportability"].get("history_coverage")
    assert history["reason_code"] == (
        None
        if status == "complete"
        else "performance_history_qualification_missing"
        if status == "missing"
        else f"{status}_history_coverage"
    )


@pytest.mark.parametrize("status,reason", [(202, "pending"), (503, "no_response")])
def test_non_success_never_attests_history_from_completed_looking_payload(
    monkeypatch, native_http_world, status, reason
):
    body = _public(monkeypatch, native_http_world, _workspace(), status=status)
    assert body["performance_history_qualification"] is None
    assert not any(row["metric"] == "return_ytd_pct" for row in body["rows"])
    assert body["unavailable_sources"] == [
        {
            "service": "lotus-performance",
            "endpoint": "/performance/workspace-summary",
            "status_code": status,
            "reason": reason,
        }
    ]


@pytest.mark.parametrize(
    "case", ["null", "text", "negative-count", "unknown-reason", "bad-calculation"]
)
def test_unusable_history_cannot_make_a_valid_return_unqualified(
    monkeypatch, native_http_world, case
):
    payload = _workspace()
    support = payload["calculation_supportability"]
    if case == "null":
        support["history_coverage"] = None
    elif case == "text":
        support["history_coverage"] = "untrusted-source-detail"
    elif case == "negative-count":
        support["history_coverage"]["missing_required_observation_count"] = -1
    elif case == "unknown-reason":
        support["history_coverage"]["reason_codes"] = ["untrusted-source-detail"]
    else:
        payload["calculation_id"] = "not-a-calculation-id"
    body = _public(monkeypatch, native_http_world, payload)
    assert [row["value"] for row in body["rows"] if row["metric"] == "return_ytd_pct"] == [5]
    assert body["unavailable_sources"] == []
    history = body["performance_history_qualification"]
    assert history["status"] == ("missing" if case == "null" else "invalid")
    assert history["client_publication_allowed"] is False and history["coverage"] is None


@pytest.mark.parametrize(
    "case", ["end-date", "calendar", "input-mode", "extra-period", "gross-string"]
)
def test_history_scope_and_unusable_additional_basis_cannot_attest_ytd_net(
    monkeypatch, native_http_world, case
):
    payload = _workspace()
    if case == "end-date":
        payload["calculation_supportability"]["history_coverage"]["requested_end_date"] = (
            "2026-01-10"
        )
    elif case == "calendar":
        payload["calculation_supportability"]["history_coverage"]["calendar_basis"] = "natural_days"
    elif case == "input-mode":
        payload["input_mode"] = "stateless"
    elif case == "extra-period":
        payload["results_by_period"]["1Y"] = copy.deepcopy(payload["results_by_period"]["YTD"])
    else:
        payload["results_by_period"]["YTD"]["portfolio_twr"]["gross"] = {
            "summary": {"cumulative_return": {"base": "5.0"}}
        }
    body = _public(monkeypatch, native_http_world, payload)
    assert [row["value"] for row in body["rows"] if row["metric"] == "return_ytd_pct"] == [5]
    assert body["unavailable_sources"] == []
    history = body["performance_history_qualification"]
    assert history["status"] == "mismatched" and history["client_publication_allowed"] is False
    assert history["reason_code"] == "performance_history_scope_mismatch"


@pytest.mark.parametrize("identity", [None, "", "FOREIGN_PORTFOLIO"])
@pytest.mark.parametrize("status", ["complete", "missing"])
def test_incompatible_source_identity_never_supplies_a_scoped_return(
    monkeypatch, native_http_world, identity, status
):
    payload = _workspace(status)
    payload["portfolio_id"] = identity
    body = _public(monkeypatch, native_http_world, payload)
    assert not any(row["metric"] == "return_ytd_pct" for row in body["rows"])
    assert body["unavailable_sources"] == [
        {
            "service": "lotus-performance",
            "endpoint": "/performance/workspace-summary",
            "status_code": 200,
            "reason": "incomplete_payload",
        }
    ]
    history = body["performance_history_qualification"]
    assert history["status"] == "mismatched" and history["client_publication_allowed"] is False
    assert history["reason_code"] == "performance_history_scope_mismatch"


@pytest.mark.parametrize(
    "state,reason,freshness",
    [
        ("degraded", "calculation_quality_issue", "current"),
        ("stale", "stale_source_observations", "stale"),
        ("untrusted-source-detail", "calculation_complete", "current"),
    ],
)
def test_complete_history_cannot_hide_qualified_source_posture(
    monkeypatch, native_http_world, state, reason, freshness
):
    payload = _workspace()
    payload["calculation_supportability"].update(
        state=state, reason=reason, freshness_bucket=freshness
    )
    body = _public(monkeypatch, native_http_world, payload)
    history = body["performance_history_qualification"]
    assert history["status"] == "complete" and history["client_publication_allowed"] is False
    assert history["source_supportability_state"] == (
        None if state == "untrusted-source-detail" else state
    )
    assert [row["value"] for row in body["rows"] if row["metric"] == "return_ytd_pct"] == [5]


def test_numeric_string_is_visible_without_manufacturing_stronger_history_attestation(
    monkeypatch, native_http_world
):
    body = _public(monkeypatch, native_http_world, _workspace(value="5.0"))
    assert [row["value"] for row in body["rows"] if row["metric"] == "return_ytd_pct"] == [5]
    history = body["performance_history_qualification"]
    assert history["status"] == "mismatched" and history["client_publication_allowed"] is False


def test_complete_baseline_exclusions_and_outside_observations_remain_source_owned(
    monkeypatch, native_http_world
):
    payload = _workspace()
    coverage = payload["calculation_supportability"]["history_coverage"]
    coverage.update(
        covered_start_date="2025-12-31",
        covered_end_date="2026-01-10",
        effective_start_date="2026-01-02",
        reason_codes=[
            "covered_window_matches_requested_window",
            "beginning_market_value_baseline_applied",
            "explicit_ignored_dates_applied",
        ],
    )
    body = _public(monkeypatch, native_http_world, payload)
    history = body["performance_history_qualification"]
    assert history["status"] == "complete" and history["client_publication_allowed"] is True
    assert history["coverage"] == coverage


@pytest.mark.parametrize("gap", ["interior", "trailing"])
def test_available_window_retains_source_owned_gap_without_reconstructing_it(
    monkeypatch, native_http_world, gap
):
    payload = _workspace("partial")
    coverage = payload["calculation_supportability"]["history_coverage"]
    coverage.update(
        covered_start_date="2026-01-01",
        effective_start_date="2026-01-01",
        missing_required_observation_count=1,
        missing_required_observation_dates_sample=["2026-01-06" if gap == "interior" else AS_OF],
        reason_codes=[f"{gap}_history_missing"],
    )
    if gap == "trailing":
        coverage.update(covered_end_date="2026-01-08", effective_end_date="2026-01-08")
    body = _public(monkeypatch, native_http_world, payload)
    history = body["performance_history_qualification"]
    assert history["status"] == "partial" and history["client_publication_allowed"] is False
    assert history["coverage"] == coverage


def test_current_benchmark_cannot_hide_unknown_portfolio_history(monkeypatch, native_http_world):
    payload = _workspace("unknown")
    payload["results_by_period"]["YTD"]["benchmark"] = {
        "benchmark_id": "CURRENT_BENCHMARK",
        "summary": {"cumulative_return": {"base": 3.0}},
    }
    body = _public(monkeypatch, native_http_world, payload)
    history = body["performance_history_qualification"]
    assert history["status"] == "unknown" and history["client_publication_allowed"] is False
    assert history["coverage"] == payload["calculation_supportability"]["history_coverage"]


def test_complete_source_history_cannot_permit_publication_of_unserializable_requested_return(
    monkeypatch, native_http_world
):
    body = _public(monkeypatch, native_http_world, _workspace(value=1e100))
    assert not any(row["metric"] == "return_ytd_pct" for row in body["rows"])
    assert body["unavailable_sources"] == [
        {
            "service": "lotus-performance",
            "endpoint": "/performance/workspace-summary",
            "status_code": 200,
            "reason": "incomplete_payload",
        }
    ]
    history = body["performance_history_qualification"]
    assert history["status"] == "complete" and history["client_publication_allowed"] is False
    assert history["reason_code"] == "performance_requested_return_unavailable"


@pytest.mark.parametrize(
    "case", ["input-mode", "arbitrary-period", "long-period", "too-many-periods"]
)
def test_unsupported_history_metadata_is_bounded_without_promoting_filtered_evidence(
    monkeypatch, native_http_world, case
):
    payload = _workspace()
    periods = payload["results_by_period"]
    if case == "input-mode":
        payload["input_mode"] = "untrusted-source-detail"
    elif case == "arbitrary-period":
        periods["untrusted-source-detail"] = copy.deepcopy(periods["YTD"])
    elif case == "long-period":
        periods["untrusted-source-detail" * 100] = copy.deepcopy(periods["YTD"])
    else:
        periods.update(
            {f"untrusted-source-detail-{i}": copy.deepcopy(periods["YTD"]) for i in range(15)}
        )
    body = _public(monkeypatch, native_http_world, payload)
    assert [row["value"] for row in body["rows"] if row["metric"] == "return_ytd_pct"] == [5]
    history = body["performance_history_qualification"]
    assert history["status"] == "invalid" and history["client_publication_allowed"] is False
    assert history["reason_code"] == "performance_history_qualification_invalid"
    assert len(history["returned_periods"]) <= 14 and len(history["period_return_bases"]) <= 14
