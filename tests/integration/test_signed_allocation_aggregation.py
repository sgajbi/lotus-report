"""Registered HTTP proofs over actual composition with controlled source clients."""

import json
import os
import socket
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import httpx
import pytest
import uvicorn

from app.main import app
from app.services.aggregation_service import AggregationService


class _Core:
    def __init__(self, values, total, weights=None):
        self.total = total
        self.currency = "USD"
        self.allocation = {
            "reporting_currency": "USD",
            "total_market_value_reporting_currency": total,
            "views": [
                {
                    "dimension": "asset_class",
                    "buckets": [
                        {
                            "dimension_value": label,
                            "market_value_reporting_currency": value,
                            **({"weight": weights[index]} if weights is not None else {}),
                        }
                        for index, (label, value) in enumerate(values)
                    ],
                }
            ],
        }
        self.calls = []

    async def get_portfolio_summary(self, portfolio_id, payload, *, admitted_tenant_id):
        assert portfolio_id == "signed-net" and admitted_tenant_id == "tenant-signed"
        assert payload["as_of_date"] == "2026-04-22"
        self.calls.append("summary")
        return 200, {
            "portfolio_id": portfolio_id,
            "reporting_currency": self.currency,
            "totals": {"total_market_value_reporting_currency": self.total},
            "snapshot_metadata": {"position_count": 2},
        }

    async def get_asset_allocation(self, portfolio_id, payload, *, admitted_tenant_id):
        assert portfolio_id == "signed-net" and admitted_tenant_id == "tenant-signed"
        assert payload == {"as_of_date": "2026-04-22", "dimensions": ["asset_class"]}
        self.calls.append("allocation")
        return 200, deepcopy(self.allocation)


class _Performance:
    async def get_workspace_summary(self, payload, *, admitted_tenant_id):
        assert admitted_tenant_id == "tenant-signed" and payload["portfolio_id"] == "signed-net"
        return 200, {
            "results_by_period": {
                "YTD": {"portfolio_twr": {"net": {"summary": {"cumulative_return": {"base": 1.0}}}}}
            }
        }


def _http(monkeypatch, core):
    service = AggregationService(core_query_client=core, performance_client=_Performance())
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
        url = f"http://127.0.0.1:{port}/aggregations/portfolios/signed-net?as_of_date=2026-04-22"
        response = httpx.get(url, headers={"X-Tenant-Id": "tenant-signed"}, timeout=10)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
    assert response.status_code == 200, response.text
    assert core.calls == ["summary", "allocation"]
    if capture := os.getenv("REPORT_SIGNED_ALLOCATION_EVIDENCE_DIR"):
        target = Path(capture)
        target.mkdir(parents=True, exist_ok=True)
        record = {
            "source_allocation": core.allocation,
            "source_summary_total": core.total,
            "source_summary_currency": core.currency,
            "public_url": url,
            "status": response.status_code,
            "response": response.json(),
            "source_client_calls": core.calls,
            "native_http_listener_closed": True,
            "source_boundary": "controlled Python Core/Performance clients; actual Report HTTP",
        }
        (target / f"case-{len(list(target.glob('case-*.json'))):02}.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
    return response.json()


@pytest.mark.parametrize(
    "values",
    [
        [("Equity", 80), ("Cash", 20)],
        [("Equity", 120), ("Cash", -20)],
        [("Equity", 110), ("Short bond", -10)],
        [("Equity", 100), ("Cash", 0)],
    ],
)
@pytest.mark.parametrize("provided", [False, True])
def test_signed_net_http_keeps_every_component_without_renormalization(
    monkeypatch, values, provided
):
    oracle = {label.upper(): Decimal(str(value)) / Decimal("100") * 100 for label, value in values}
    weights = [str(Decimal(str(value)) / 100) for _, value in values] if provided else None
    body = _http(monkeypatch, _Core(values, 100, weights))
    actual = {
        row["bucket"]: Decimal(str(row["value"]))
        for row in body["rows"]
        if row["metric"] == "weight_pct"
    }
    assert actual == oracle and sum(actual.values()) == 100
    assert body["unavailable_sources"] == []
    qualification = body["allocation_supportability"]
    assert qualification["status"] == "available"
    assert qualification["basis"] == "signed_net_reporting_currency"
    assert qualification["weight_source"] == ("source" if provided else "derived")
    assert qualification["reporting_currency"] == "USD"


@pytest.mark.parametrize("total", [0, -100])
def test_nonpositive_denominator_is_explicit_even_with_source_weights(monkeypatch, total):
    body = _http(monkeypatch, _Core([("Equity", 120), ("Cash", -20)], total, [1.2, -0.2]))
    assert not any(row["metric"] == "weight_pct" for row in body["rows"])
    assert body["allocation_supportability"]["reason_code"] == "nonpositive_net_denominator"
    assert body["allocation_supportability"]["status"] == "unavailable"
    assert any(
        row["metric"] == "market_value_base" and row["value"] == total for row in body["rows"]
    )


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("null_weight", "incomplete_allocation_payload"),
        ("invalid_weight", "incomplete_allocation_payload"),
        ("null_value", "incomplete_allocation_payload"),
        ("nan_value", "incomplete_allocation_payload"),
        ("infinite_weight", "incomplete_allocation_payload"),
        ("bool_value", "incomplete_allocation_payload"),
        ("inconsistent_weight", "source_weight_mismatch"),
        ("different_currency", "reporting_currency_mismatch"),
        ("missing_currency", "reporting_currency_unavailable"),
        ("missing_summary_currency", "reporting_currency_unavailable"),
        ("different_denominator", "source_denominator_mismatch"),
        ("partial_coverage", "source_valuation_unavailable"),
    ],
)
def test_unsupported_source_never_publishes_a_partial_confident_breakdown(
    monkeypatch, mutation, reason
):
    core = _Core([("Equity", 120), ("Cash", -20)], 100, [1.2, -0.2])
    first = core.allocation["views"][0]["buckets"][0]
    if mutation == "null_weight":
        first["weight"] = None
    elif mutation == "invalid_weight":
        first["weight"] = "n/a"
    elif mutation == "null_value":
        first["market_value_reporting_currency"] = None
    elif mutation == "nan_value":
        first["market_value_reporting_currency"] = "NaN"
    elif mutation == "infinite_weight":
        first["weight"] = "Infinity"
    elif mutation == "bool_value":
        first["market_value_reporting_currency"] = True
    elif mutation == "inconsistent_weight":
        first["weight"] = 0.8
    elif mutation == "different_currency":
        core.allocation["reporting_currency"] = "EUR"
    elif mutation == "missing_currency":
        del core.allocation["reporting_currency"]
    elif mutation == "missing_summary_currency":
        core.currency = None
    elif mutation == "different_denominator":
        core.allocation["total_market_value_reporting_currency"] = 200
    else:
        core.allocation["valuation_coverage"] = {"coverage_state": "PARTIAL"}
    body = _http(monkeypatch, core)
    assert not any(row["metric"] == "weight_pct" for row in body["rows"])
    assert body["allocation_supportability"]["status"] == "unavailable"
    assert body["allocation_supportability"]["reason_code"] == reason
