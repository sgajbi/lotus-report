"""Native registered Report HTTP with controlled Core/Performance source DTOs."""

import copy
import json
import os
import socket
from decimal import Decimal
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import httpx
import pytest
import uvicorn

from app.main import app
from app.observability import propagation_headers
from app.services.aggregation_service import AggregationService

PORTFOLIO = "synthetic-performance"
TENANT = "synthetic-tenant"
AS_OF = "2026-04-22"
CORRELATION = "synthetic-correlation391"
TRACE = "0123456789abcdef0123456789abcdef"
PATH = ("results_by_period", "YTD", "portfolio_twr", "net", "summary", "cumulative_return", "base")


def _payload(value):
    result = value
    for key in reversed(PATH):
        result = {key: result}
    result["portfolio_id"] = PORTFOLIO
    return result


def _record_context(calls, endpoint, payload, admitted_tenant_id):
    headers = propagation_headers()
    assert admitted_tenant_id == TENANT
    assert headers["X-Correlation-Id"] == CORRELATION
    assert headers["X-Trace-Id"] == TRACE
    calls.append(
        {
            "endpoint": endpoint,
            "payload": payload,
            "tenant": admitted_tenant_id,
            "correlation": headers["X-Correlation-Id"],
            "trace": headers["X-Trace-Id"],
        }
    )


class _Core:
    def __init__(self, calls, position_count=1):
        self.calls, self.position_count = calls, position_count

    async def get_portfolio_summary(self, portfolio_id, payload, *, admitted_tenant_id):
        assert portfolio_id == PORTFOLIO and payload == {"as_of_date": AS_OF}
        _record_context(self.calls, "summary", payload, admitted_tenant_id)
        return 200, {
            "reporting_currency": "USD",
            "totals": {"total_market_value_reporting_currency": "100"},
            "snapshot_metadata": {"position_count": self.position_count},
        }

    async def get_asset_allocation(self, portfolio_id, payload, *, admitted_tenant_id):
        assert portfolio_id == PORTFOLIO
        assert payload == {"as_of_date": AS_OF, "dimensions": ["asset_class"]}
        _record_context(self.calls, "allocation", payload, admitted_tenant_id)
        return 200, {
            "reporting_currency": "USD",
            "total_market_value_reporting_currency": "100",
            "views": [
                {
                    "dimension": "asset_class",
                    "buckets": [
                        {
                            "dimension_value": "Equity",
                            "market_value_reporting_currency": "100",
                            "weight": "1",
                        }
                    ],
                }
            ],
        }


class _Performance:
    def __init__(self, calls, status, payload):
        self.calls, self.status, self.payload = calls, status, payload

    async def get_workspace_summary(self, payload, *, admitted_tenant_id):
        assert payload == {
            "portfolio_id": PORTFOLIO,
            "report_end_date": AS_OF,
            "input_mode": "stateful",
            "stateful_input": {},
            "periods": [{"period": "YTD", "frequencies": ["daily"]}],
        }
        _record_context(self.calls, "performance", payload, admitted_tenant_id)
        return self.status, self.payload


@pytest.fixture(scope="module")
def http_client():
    # Reuse client configuration while every case keeps its own native listener.
    with httpx.Client() as client:
        yield client


def _http(monkeypatch, http_client, performance_payload, status=200, position_count=1):
    calls = []
    source_before = repr(performance_payload)
    service = AggregationService(
        core_query_client=_Core(calls, position_count),
        performance_client=_Performance(calls, status, performance_payload),
    )
    monkeypatch.setattr("app.routers.aggregations.AggregationService", lambda: service)
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
        url = f"http://127.0.0.1:{port}/aggregations/portfolios/{PORTFOLIO}?as_of_date={AS_OF}"
        response = http_client.get(
            url,
            headers={"X-Tenant-Id": TENANT, "X-Correlation-Id": CORRELATION, "X-Trace-Id": TRACE},
            timeout=10,
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
    assert response.status_code == 200, response.text
    assert response.headers["X-Correlation-Id"] == CORRELATION
    assert response.headers["X-Trace-Id"] == TRACE
    assert [call["endpoint"] for call in calls] == ["summary", "allocation", "performance"]
    assert repr(performance_payload) == source_before
    body = response.json()
    json.dumps(body, allow_nan=False)
    assert body["scope"] == {"portfolio_id": PORTFOLIO, "as_of_date": AS_OF}
    assert any(row["metric"] == "market_value_base" and row["value"] == 100 for row in body["rows"])
    assert any(
        row["bucket"] == "EQUITY" and row["metric"] == "weight_pct" and row["value"] == 100
        for row in body["rows"]
    )
    assert body["allocation_supportability"]["status"] == "available"
    assert "untrusted-source-detail" not in response.text
    if capture := os.getenv("REPORT_PERFORMANCE_AGGREGATION_EVIDENCE_DIR"):
        target = Path(capture)
        target.mkdir(parents=True, exist_ok=True)
        record = {
            "source_performance_status": status,
            "source_performance_python_repr": source_before,
            "source_position_count": position_count,
            "source_client_calls": calls,
            "public_url": url,
            "response_status": response.status_code,
            "response": body,
            "native_http_listener_closed": True,
            "source_boundary": "controlled Python source DTOs; actual registered Report HTTP",
        }
        (target / f"case-{len(list(target.glob('case-*.json'))):03}.json").write_text(
            json.dumps(record, indent=2, allow_nan=False), encoding="utf-8"
        )
    return body


def _performance_qualifiers(body):
    return [
        source for source in body["unavailable_sources"] if source["service"] == "lotus-performance"
    ]


def _assert_incomplete(body):
    assert not any(row["metric"] == "return_ytd_pct" for row in body["rows"])
    assert _performance_qualifiers(body) == [
        {
            "service": "lotus-performance",
            "endpoint": "/performance/workspace-summary",
            "status_code": 200,
            "reason": "incomplete_payload",
        }
    ]


@pytest.mark.parametrize("key", PATH[:-1])
@pytest.mark.parametrize("kind", ["missing", "null", "list", "text", "boolean"])
def test_every_incomplete_success_container_is_explicitly_qualified(
    monkeypatch, http_client, key, kind
):
    payload = _payload("1.25")
    cursor = payload
    for part in PATH[: PATH.index(key)]:
        cursor = cursor[part]
    if kind == "missing":
        cursor.pop(key)
    else:
        cursor[key] = {
            "null": None,
            "list": [],
            "text": "untrusted-source-detail",
            "boolean": True,
        }[kind]
    _assert_incomplete(_http(monkeypatch, http_client, payload))


@pytest.mark.parametrize("payload", [None, [], "untrusted-source-detail"])
def test_non_object_success_payload_is_qualified(monkeypatch, http_client, payload):
    _assert_incomplete(_http(monkeypatch, http_client, payload))


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        [],
        {},
        "untrusted-source-detail",
        "NaN",
        "sNaN",
        "Infinity",
        "-Infinity",
        float("nan"),
        float("inf"),
        Decimal("NaN"),
        Decimal("sNaN"),
        "1E+400",
    ],
)
def test_unusable_success_scalar_is_qualified_without_a_fabricated_return(
    monkeypatch, http_client, value
):
    _assert_incomplete(_http(monkeypatch, http_client, _payload(value)))


def test_missing_base_is_qualified(monkeypatch, http_client):
    payload = _payload("1.25")
    cursor = payload
    for key in PATH[:-1]:
        cursor = cursor[key]
    cursor.pop("base")
    _assert_incomplete(_http(monkeypatch, http_client, payload))


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 0),
        ("0", 0),
        (0.0, 0),
        (Decimal("0"), 0),
        ("1.25", 1.25),
        (-2.75, -2.75),
        ("1.2345675", 1.234568),
        ("1.2345665", 1.234566),
    ],
)
def test_finite_zero_signed_and_precision_neighbors_remain_measured(
    monkeypatch, http_client, value, expected
):
    body = _http(monkeypatch, http_client, _payload(value))
    assert body["unavailable_sources"] == []
    assert [row["value"] for row in body["rows"] if row["metric"] == "return_ytd_pct"] == [expected]


@pytest.mark.parametrize("status,reason", [(202, "pending"), (503, "no_response")])
@pytest.mark.parametrize("payload", [{}, _payload("0"), _payload("1.25")])
def test_non_success_status_is_preserved_even_with_completed_looking_payload(
    monkeypatch, http_client, status, reason, payload
):
    body = _http(monkeypatch, http_client, copy.deepcopy(payload), status=status)
    assert not any(row["metric"] == "return_ytd_pct" for row in body["rows"])
    assert _performance_qualifiers(body) == [
        {
            "service": "lotus-performance",
            "endpoint": "/performance/workspace-summary",
            "status_code": status,
            "reason": reason,
        }
    ]


def test_core_count_and_performance_gaps_remain_independently_qualified(monkeypatch, http_client):
    body = _http(monkeypatch, http_client, {}, position_count=None)
    _assert_incomplete(body)
    assert not any(row["metric"] == "position_count" for row in body["rows"])
    assert [
        source for source in body["unavailable_sources"] if source["service"] == "lotus-core"
    ] == [
        {
            "service": "lotus-core",
            "endpoint": "/reporting/portfolio-summary/query",
            "status_code": 200,
            "reason": "incomplete_payload",
        }
    ]


@pytest.mark.parametrize("alternative", ["period", "basis"])
def test_another_measured_period_or_basis_cannot_replace_requested_ytd_net(
    monkeypatch, http_client, alternative
):
    payload = _payload("1.25")
    if alternative == "period":
        results = payload["results_by_period"]
        results["1Y"] = results.pop("YTD")
    else:
        twr = payload["results_by_period"]["YTD"]["portfolio_twr"]
        twr["gross"] = twr.pop("net")
    _assert_incomplete(_http(monkeypatch, http_client, payload))
