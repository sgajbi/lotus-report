"""Durable capture evidence refuses mismatched call identities and lost failures."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.composite_reporting.correction_notice.errors import NoticeEvidenceRefused
from app.reporting_lineage.capture_service import (
    _capture_integrity_error,
    _map_job_failure,
    _RecordingAiClient,
    _RecordingRiskClient,
    _stored_capture_failure,
    _UpstreamRecorder,
)


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("boolean_count", "lineage_call_count_invalid"),
        ("string_count", "lineage_call_count_invalid"),
        ("services_shape", "lineage_source_services_invalid"),
        ("service", "lineage_source_services_mismatch"),
        ("correlation", "lineage_correlation_id_mismatch"),
        ("trace", "lineage_trace_id_mismatch"),
        ("valid", None),
    ],
)
def test_capture_integrity_binds_call_count_services_correlation_and_trace(fault, expected):
    snapshot = SimpleNamespace(
        lineage_summary={"call_count": 1, "source_services": ["lotus-report"]},
        correlation_id="corr",
        trace_id="trace",
    )
    call = SimpleNamespace(service_name="lotus-report", correlation_id="corr", trace_id="trace")
    if fault.endswith("count"):
        snapshot.lineage_summary["call_count"] = True if fault.startswith("boolean") else "1"
    elif fault == "services_shape":
        snapshot.lineage_summary["source_services"] = "lotus-report"
    elif fault == "service":
        call.service_name = "foreign"
    elif fault in {"correlation", "trace"}:
        setattr(call, fault + "_id", "foreign")
    assert _capture_integrity_error(snapshot, [call]) == expected


@pytest.mark.parametrize(
    "method,endpoint",
    [
        ("historical_attribution", "/analytics/risk/historical-attribution"),
        ("rolling_metrics", "/analytics/risk/rolling-metrics"),
    ],
)
@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.asyncio
async def test_risk_capture_records_success_and_failure_with_admitted_tenant(
    method, endpoint, failure
):
    recorder = _UpstreamRecorder(correlation_id="corr", trace_id="trace")
    error = TimeoutError("owner unavailable")
    inner = AsyncMock(side_effect=error if failure else None, return_value=(200, {"value": "1"}))
    client = _RecordingRiskClient(SimpleNamespace(**{method: inner}), recorder)
    if failure:
        with pytest.raises(TimeoutError) as caught:
            await getattr(client, method)({"input_mode": "stateful"}, admitted_tenant_id="tenant")
        assert caught.value is error
    else:
        assert await getattr(client, method)(
            {"input_mode": "stateful"}, admitted_tenant_id="tenant"
        ) == (200, {"value": "1"})
    inner.assert_awaited_once_with({"input_mode": "stateful"}, admitted_tenant_id="tenant")
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call.endpoint == endpoint
    assert call.correlation_id == "corr" and call.trace_id == "trace"
    assert call.status_code == (504 if failure else 200)
    if failure:
        assert call.failure_category == "timeout"


@pytest.mark.asyncio
async def test_ai_capture_failure_is_recorded_before_propagating():
    recorder = _UpstreamRecorder(correlation_id="corr", trace_id="trace")
    error = TimeoutError("accepted-output unavailable")
    inner = AsyncMock(side_effect=error)
    client = _RecordingAiClient(SimpleNamespace(get_accepted_workflow_output=inner), recorder)
    with pytest.raises(TimeoutError) as caught:
        await client.get_accepted_workflow_output("run", tenant_id="tenant")
    assert caught.value is error
    assert len(recorder.calls) == 1
    assert recorder.calls[0].failure_category == "timeout"
    assert recorder.calls[0].request_payload == {"run_id": "run"}


def test_notice_refusal_remains_nonretryable_and_corrupt_stored_failure_defaults_safely():
    assert _map_job_failure(NoticeEvidenceRefused("missing_evidence")) == (
        "validation_failed",
        "missing_evidence",
        False,
    )
    assert _stored_capture_failure({"failure_category": "archived", "failure_message": " "}) == (
        "upstream_data_failed",
        "Upstream report-data capture failed.",
        True,
    )
