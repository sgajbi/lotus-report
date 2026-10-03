"""Real Report HTTP with unchanged clients and controlled Core ownership/facts.

This certifies Report admission and forwarding, not Core authorization or valuation.
"""

import json
import os
import socket
import subprocess
import sys
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit

import httpx
import pytest

PORTFOLIOS = {"SUMMARY_A": ("tenant-a", 2400.0, 1320.0), "SUMMARY_B": ("tenant-b", 800.0, 440.0)}
AS_OF = "2026-04-10"
MISSING_DETAIL = {
    "code": "missing_caller_context",
    "message": "Required caller context headers are missing.",
    "missing_headers": ["X-Tenant-Id"],
}


@pytest.fixture(scope="module")
def summary_http(tmp_path_factory):
    calls = []
    exchanges = []

    def capture_response(response):
        if "/summary" in response.request.url.path:
            response.read()
            exchanges.append(
                {
                    "method": response.request.method,
                    "url": str(response.request.url),
                    "headers": dict(response.request.headers),
                    "body": json.loads(response.request.content),
                    "status": response.status_code,
                    "response": response.json(),
                }
            )

    class CoreHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size)) if size else None
            path = urlsplit(self.path).path
            portfolio = (
                body.get("portfolio_id") or body.get("scope", {}).get("portfolio_id")
                if body
                else path.split("/")[2]
            )
            tenant = self.headers.get("X-Tenant-Id")
            calls.append(
                {
                    "path": path,
                    "body": body,
                    "portfolio": portfolio,
                    "tenant": tenant,
                    "correlation": self.headers.get("X-Correlation-ID"),
                    "trace": self.headers.get("X-Trace-ID"),
                }
            )
            owner, total, cash = PORTFOLIOS.get(portfolio, (None, None, None))
            if not tenant:
                code, payload = 401, {"detail": "TENANT_CONTEXT_REQUIRED"}
            elif tenant != owner:
                code, payload = 404, {"detail": "Portfolio not found"}
            elif path == "/reporting/portfolio-summary/query":
                code, payload = (
                    200,
                    {
                        "portfolio_id": portfolio,
                        "totals": {
                            "total_market_value_reporting_currency": total,
                            "cash_balance_reporting_currency": cash,
                        },
                        "snapshot_metadata": {"currency": "USD", "snapshot_date": AS_OF},
                    },
                )
            elif path == "/reporting/asset-allocation/query":
                code, payload = (
                    200,
                    {
                        "views": [
                            {
                                "dimension": "asset_class",
                                "buckets": [
                                    {
                                        "dimension_value": "Cash",
                                        "weight": 0.55,
                                        "market_value_reporting_currency": cash,
                                        "position_count": 1,
                                    },
                                    {
                                        "dimension_value": "Equity",
                                        "weight": 0.45,
                                        "market_value_reporting_currency": total - cash,
                                        "position_count": 1,
                                    },
                                ],
                            }
                        ]
                    },
                )
            elif path.endswith("/positions"):
                code, payload = 200, {"positions": []}
            elif path.endswith("/transactions"):
                code, payload = 200, {"transactions": [], "total": 0}
            else:
                code, payload = 503, {"detail": "Controlled source unavailable"}
            content = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    core = ThreadingHTTPServer(("127.0.0.1", 0), CoreHandler)
    thread = Thread(target=core.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    root = Path(__file__).resolve().parents[2]
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONPATH": str(root / "src"),
            "LOTUS_CORE_QUERY_BASE_URL": f"http://127.0.0.1:{core.server_port}",
            "UPSTREAM_MAX_RETRIES": "0",
        }
    )
    log_path = tmp_path_factory.mktemp("summary-http") / "report.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=root,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                timeout=5,
                event_hooks={"response": [capture_response]},
            ) as client:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        pytest.fail(log_path.read_text())
                    try:
                        if client.get("/health/live").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail("Report listener did not start: " + log_path.read_text())
                yield client, calls, exchanges
        finally:
            process.terminate()
            process.wait(timeout=10)
            core.shutdown()
            core.server_close()
            thread.join(timeout=5)
            assert not thread.is_alive()
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", port)) != 0
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", core.server_port)) != 0
            evidence_directory = os.environ.get("REPORT_SUMMARY_HTTP_EVIDENCE_DIR")
            if evidence_directory:
                evidence_path = Path(evidence_directory)
                evidence_path.mkdir(parents=True, exist_ok=True)
                (evidence_path / "network.json").write_text(
                    json.dumps(
                        {
                            "evidence_class": "native_report_http_controlled_core",
                            "report_requests": exchanges,
                            "source_requests": calls,
                            "report_listener": port,
                            "source_listener": core.server_port,
                            "cleanup_verified": True,
                            "boundary": "Native Report; controlled Core facts.",
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )


@pytest.mark.parametrize("portfolio", PORTFOLIOS)
def test_owner_tenant_reaches_every_summary_source(summary_http, portfolio):
    client, calls, _exchanges = summary_http
    owner, total, cash = PORTFOLIOS[portfolio]
    start = len(calls)
    response = client.post(
        f"/reports/portfolios/{portfolio}/summary",
        headers={
            "X-Tenant-Id": owner,
            "X-Correlation-ID": "summary-owner-correlation",
            "X-Trace-ID": "summary-owner-trace",
        },
        json={
            "as_of_date": AS_OF,
            "sections": ["WEALTH", "ALLOCATION", "PNL", "INCOME", "ACTIVITY"],
            "tenant_id": "forged-body-tenant",
            "admitted_tenant_id": "forged-body-tenant",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["wealth"] == {"total_market_value": total, "total_cash": cash}
    assert payload["allocation"]["byAssetClass"] == [
        {"group": "Cash", "weight": 0.55, "market_value": cash, "position_count": 1},
        {"group": "Equity", "weight": 0.45, "market_value": total - cash, "position_count": 1},
    ]
    selected_calls = calls[start:]
    assert {call["path"] for call in selected_calls} == {
        "/reporting/portfolio-summary/query",
        "/reporting/asset-allocation/query",
        f"/portfolios/{portfolio}/positions",
        f"/portfolios/{portfolio}/transactions",
    }
    assert len(selected_calls) == 4
    assert all(
        call["tenant"] == owner and call["portfolio"] == portfolio for call in selected_calls
    )
    assert all(
        call["correlation"] == "summary-owner-correlation"
        and call["trace"] == "summary-owner-trace"
        for call in selected_calls
    )
    assert "forged-body-tenant" not in json.dumps(selected_calls)


@pytest.mark.parametrize("tenant", [None, "", "   "])
def test_missing_tenant_refuses_before_source_io(summary_http, tenant):
    client, calls, exchanges = summary_http
    start = len(calls)
    # httpx/h11 refuses whitespace-only header values before transmission.
    # A real HTTP request via the standard-library client reaches Report's boundary.
    connection = HTTPConnection(client.base_url.host, client.base_url.port, timeout=5)
    try:
        connection.request(
            "POST",
            "/reports/portfolios/SUMMARY_A/summary",
            body=json.dumps({"as_of_date": AS_OF, "tenant_id": "tenant-a"}),
            headers={
                "Content-Type": "application/json",
                **({} if tenant is None else {"X-Tenant-Id": tenant}),
            },
        )
        wire_response = connection.getresponse()
        response = httpx.Response(wire_response.status, content=wire_response.read())
    finally:
        connection.close()
    exchanges.append(
        {
            "method": "POST",
            "url": "/reports/portfolios/SUMMARY_A/summary",
            "headers": {} if tenant is None else {"X-Tenant-Id": tenant},
            "body": {"as_of_date": AS_OF, "tenant_id": "tenant-a"},
            "status": response.status_code,
            "response": response.json(),
        }
    )
    assert response.status_code == 400, response.text
    assert response.json() == {"detail": MISSING_DETAIL}
    assert len(calls) == start


@pytest.mark.parametrize(
    "portfolio,foreign", [("SUMMARY_A", "tenant-b"), ("SUMMARY_B", "tenant-a")]
)
def test_foreign_tenant_retains_source_refusal_without_financial_disclosure(
    summary_http, portfolio, foreign
):
    client, calls, _exchanges = summary_http
    start = len(calls)
    response = client.post(
        f"/reports/portfolios/{portfolio}/summary",
        headers={"X-Tenant-Id": foreign},
        json={"as_of_date": AS_OF, "sections": ["WEALTH", "ALLOCATION"]},
    )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Portfolio not found"}
    assert len(calls) == start + 1
    assert calls[-1]["tenant"] == foreign


def test_summary_section_limit_retains_bounds(summary_http):
    client, calls, _exchanges = summary_http
    start = len(calls)
    response = client.post(
        "/reports/portfolios/SUMMARY_A/summary?section_limit=1",
        headers={"X-Tenant-Id": "tenant-a"},
        json={"as_of_date": AS_OF, "sections": ["WEALTH", "ALLOCATION"]},
    )
    assert response.status_code == 200
    assert "wealth" in response.json() and "allocation" not in response.json()
    assert len(calls) == start + 1


def test_summary_openapi_documents_tenant_and_local_refusal(summary_http):
    client, _calls, _exchanges = summary_http
    operation = client.get("/openapi.json").json()["paths"][
        "/reports/portfolios/{portfolio_id}/summary"
    ]["post"]
    tenant = next(
        parameter for parameter in operation["parameters"] if parameter["name"] == "X-Tenant-Id"
    )
    assert tenant["in"] == "header"
    assert tenant["required"] is True
    assert tenant["schema"] == {"type": "string", "minLength": 1, "pattern": r"\S"}
    assert sum(parameter["name"] == "X-Tenant-Id" for parameter in operation["parameters"]) == 1
    assert "Required" in tenant["description"]
    assert operation["responses"]["400"]["content"]["application/json"]["example"] == {
        "detail": MISSING_DETAIL
    }
