"""Current Risk wire through native client, capture, reader and Render admission."""

import json
from copy import deepcopy

import httpx
import pytest

from app.clients.risk_client import RiskClient
from app.reporting_lineage.capture_service import _RecordingRiskClient, _UpstreamRecorder
from app.reporting_render.risk_posture import build_risk_posture
from app.services.reporting_read_service import ReportingReadService
from tests.unit.test_reporting_read_service import (
    _CoreQueryClientSuccess,
    _PerformanceClientSuccess,
    _RiskClientSuccess,
)

STATES = [
    ("ready", "calculation_complete", "ready", "complete"),
    ("stale", "stale_source_observations", "partial", "partial"),
    ("degraded", "calculation_quality_issue", "partial", "partial"),
    ("empty", "no_return_observations", "unavailable", "unavailable"),
    ("error", "calculation_quality_issue", "unavailable", "error"),
    ("permission_blocked", "permission_blocked", "unavailable", "unavailable"),
    ("unsupported", "unsupported_input_mode", "unavailable", "not_supported"),
]


async def risk_wire(request, route, state="ready", reason="calculation_complete"):
    source = _RiskClientSuccess()
    method = source.rolling_metrics if route.endswith("rolling-metrics") else source.calculate_risk
    _, response = await method(request, admitted_tenant_id="tenant-sg")
    scope = request["stateful_input"]
    response["scope"] = {
        key: scope.get(key) for key in ("as_of_date", "reporting_currency", "net_or_gross")
    }
    response["metadata"].update(
        contract_version="v1",
        calculation_supportability={
            "state": state,
            "reason": reason,
            "freshness_bucket": "stale" if state == "stale" else "current",
        },
        source_returns_evidence={
            "source_service": "lotus-performance",
            "calculation_id": "00000000-0000-4000-8000-000000000001",
            "contract_version": "v1",
            "input_fingerprint": "sha256:" + "1" * 64,
            "calculation_hash": "sha256:" + "2" * 64,
            "freshness": "stale" if state == "stale" else "current",
            "requested_points": 270,
            "returned_points": 269 if state == "degraded" else 270,
            "missing_points": 1 if state == "degraded" else 0,
            "coverage_ratio": 0.9962963 if state == "degraded" else 1.0,
        },
    )
    return response


async def admitted_block(
    monkeypatch, route, state, reason, mutation=None, fallback_period=None, status_code=200
):
    wires = []

    async def handle(request):
        assert request.headers["X-Tenant-Id"] == "tenant-sg"
        payload = json.loads(request.content)
        periods = payload["stateful_input"]["periods"]
        if fallback_period and len(periods) > 1:
            return httpx.Response(500, json={"error": "Controlled combined-request failure"})
        limited = not fallback_period or periods[0]["name"] == fallback_period
        response = await risk_wire(
            payload,
            request.url.path,
            state if limited else "ready",
            reason if limited else "calculation_complete",
        )
        if mutation:
            mutation(response)
        wires.append(deepcopy(response))
        return httpx.Response(status_code, json=response)

    native_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: native_client(
            *args, transport=httpx.MockTransport(handle), **kwargs
        ),
    )
    recorder = _UpstreamRecorder(correlation_id="risk-wire", trace_id="risk-trace")
    client = _RecordingRiskClient(RiskClient("http://risk", 1, max_retries=0), recorder)
    performance = _PerformanceClientSuccess()
    service = ReportingReadService(
        core_query_client=_CoreQueryClientSuccess(),
        performance_client=performance,
        risk_client=client,
    )
    request = {"reporting_currency": "USD", "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40"}
    if route == "calculate":
        _, workspace = await performance.get_workspace_summary(
            {"portfolio_id": "P1"}, admitted_tenant_id="tenant-sg"
        )
        block = await service._build_risk_analytics(
            "P1",
            "2026-02-24",
            request,
            workspace_summary_payload=workspace,
            admitted_tenant_id="tenant-sg",
        )
    else:
        block = await service._build_risk_trend(
            "P1",
            "2026-02-24",
            request,
            admitted_tenant_id="tenant-sg",
        )
    return block, recorder.calls, wires


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["calculate", "rolling"])
@pytest.mark.parametrize("state,reason,report_status,capture_status", STATES)
async def test_native_wire_retains_state_identity_figures_and_render_reason(
    monkeypatch, route, state, reason, report_status, capture_status
):
    block, calls, wires = await admitted_block(monkeypatch, route, state, reason)
    assert len(calls) == len(wires) == 1
    assert calls[0].supportability_status == capture_status
    assert calls[0].response_payload == wires[0]
    assert block["results"] == wires[0]["results"]
    assert (
        block["metadata"]["source_returns_evidence"]
        == wires[0]["metadata"]["source_returns_evidence"]
    )
    assert block["supportability"]["status"] == report_status
    assert block["supportability"]["source_qualification"]["state"] == state
    if state != "ready":
        assert reason in {note["code"] for note in block["supportability"]["notes"]}
        snapshot = (
            {"riskAnalytics": block}
            if route == "calculate"
            else {
                "riskAnalytics": {"supportability": {"status": "ready", "notes": []}},
                "riskTrend": block,
            }
        )
        assert reason in {note["code"] for note in build_risk_posture(snapshot)["notes"]}
    if state == "degraded":
        assert block["metadata"]["source_returns_evidence"]["coverage_ratio"] == 0.9962963


def mutate_missing(response):
    response["metadata"].pop("calculation_supportability")


def mutate_foreign_scope(response):
    response["scope"]["as_of_date"] = "2026-02-25"


def mutate_foreign_owner(response):
    response["metadata"]["source_returns_evidence"]["source_service"] = "foreign-service"


def mutate_bad_hash(response):
    response["metadata"]["source_returns_evidence"]["calculation_hash"] = "not-a-hash"


def mutate_bad_counts(response):
    response["metadata"]["source_returns_evidence"]["missing_points"] = 1


def mutate_unknown_state(response):
    response["metadata"]["calculation_supportability"]["state"] = "looks-ready"


def mutate_ready_with_partial_source(response):
    response["metadata"]["source_returns_evidence"].update(
        returned_points=269, missing_points=1, coverage_ratio=0.9962963
    )


def mutate_unsafe_reason(response):
    response["metadata"]["calculation_supportability"]["reason"] = "private-path/customer-identity"


def mutate_bad_ratio(response):
    response["metadata"]["source_returns_evidence"]["coverage_ratio"] = 0.9


def mutate_boolean_count(response):
    response["metadata"]["source_returns_evidence"]["requested_points"] = True


def mutate_missing_source(response):
    response["metadata"].pop("source_returns_evidence")


def mutate_foreign_basis(response):
    response["scope"]["net_or_gross"] = "GROSS"


def mutate_near_quantized_ratio(response):
    response["metadata"]["source_returns_evidence"].update(
        returned_points=269, missing_points=1, coverage_ratio=0.9962963001
    )


def mutate_bad_calculation_id(response):
    response["metadata"]["source_returns_evidence"]["calculation_id"] = "foreign-handle"


def mutate_unknown_freshness(response):
    response["metadata"]["source_returns_evidence"]["freshness"] = "unknown"


def mutate_foreign_contract(response):
    response["metadata"]["source_returns_evidence"]["contract_version"] = "v2"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["calculate", "rolling"])
@pytest.mark.parametrize(
    "mutation",
    [
        mutate_missing,
        mutate_foreign_scope,
        mutate_foreign_owner,
        mutate_bad_hash,
        mutate_bad_counts,
        mutate_unknown_state,
        mutate_ready_with_partial_source,
        mutate_unsafe_reason,
        mutate_bad_ratio,
        mutate_boolean_count,
        mutate_missing_source,
        mutate_foreign_basis,
        mutate_near_quantized_ratio,
        mutate_bad_calculation_id,
        mutate_unknown_freshness,
        mutate_foreign_contract,
    ],
)
async def test_missing_malformed_or_foreign_authority_never_becomes_ready(
    monkeypatch, route, mutation
):
    block, calls, _ = await admitted_block(
        monkeypatch, route, "ready", "calculation_complete", mutation
    )
    assert calls[0].supportability_status != "complete"
    assert block["supportability"]["status"] != "ready"
    assert block["supportability"]["source_qualification"]["state"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["calculate", "rolling"])
async def test_unrelated_payload_words_do_not_define_risk_authority(monkeypatch, route):
    def unrelated(response):
        response["metadata"]["unrelated_description"] = (
            "redacted unsupported partial missing_fields"
        )

    block, calls, _ = await admitted_block(
        monkeypatch, route, "ready", "calculation_complete", unrelated
    )
    assert calls[0].supportability_status == "complete"
    assert block["supportability"]["status"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["calculate", "rolling"])
@pytest.mark.parametrize("status_code", [302, 400, 500])
async def test_non_success_transport_cannot_admit_ready_payload(monkeypatch, route, status_code):
    block, calls, _ = await admitted_block(
        monkeypatch, route, "ready", "calculation_complete", status_code=status_code
    )
    assert all(call.supportability_status != "complete" for call in calls)
    assert block["supportability"]["status"] == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("limited_period", ["YTD", "1Y"])
async def test_per_period_fallback_cannot_erase_a_qualified_limitation(monkeypatch, limited_period):
    block, calls, _ = await admitted_block(
        monkeypatch,
        "calculate",
        "stale",
        "stale_source_observations",
        fallback_period=limited_period,
    )
    assert len(calls) == 3
    assert block["supportability"]["status"] == "partial"
    qualification = block["supportability"]["source_qualification"]
    assert qualification["state"] == "stale"
    assert {period["qualification"]["state"] for period in qualification["periods"]} == {
        "ready",
        "stale",
    }
