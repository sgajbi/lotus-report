from copy import deepcopy
from datetime import UTC, datetime

import httpx
import pytest

from app.clients.performance_client import PerformanceClient
from app.composite_reporting.source_capture import CompositeInputProvider
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_lineage.capture_service import PortfolioReviewInputCaptureError
from tests.unit.composite_reporting.fixtures import calculated_example, selection_for


def job_for(payload):
    selection = selection_for(payload)
    now = datetime(2026, 10, 9, tzinfo=UTC)
    return ReportJobLedgerRecord(
        request_id="rrq_composite",
        job_id="rjob_composite",
        report_type="composite_review",
        portfolio_scope={"composite_id": selection.composite_id},
        requested_output_formats=["xlsx"],
        as_of_date=selection.period_end,
        reporting_currency=selection.reporting_currency,
        options={"composite_selection": selection.model_dump(mode="json")},
        trigger_type="user",
        triggered_by="actor-a",
        caller_application="lotus-gateway",
        tenant_id="tenant-a",
        region="APAC",
        idempotency_key="composite-original",
        request_hash="sha256:request",
        status="accepted",
        current_step="accepted",
        retry_eligible=False,
        cancel_requested=False,
        created_at=now,
        updated_at=now,
        correlation_id="corr-composite",
        trace_id="trace-composite",
    )


@pytest.mark.asyncio
async def test_exact_pins_tenant_and_trace_reach_actual_client_transport(monkeypatch):
    payload = calculated_example()
    job = job_for(payload)
    sent = []

    async def transport(**kwargs):
        sent.append(deepcopy(kwargs))
        return 200, payload

    monkeypatch.setattr("app.clients.performance_client.post_with_retry", transport)
    client = PerformanceClient(base_url="http://performance", timeout_seconds=1, max_retries=0)
    capture = await CompositeInputProvider(performance_client=client).collect_for_job(job)
    assert sent[0]["url"] == "http://performance/composites/twr"
    assert sent[0]["json_body"] == selection_for(payload).performance_request()
    assert sent[0]["headers"]["X-Tenant-Id"] == "tenant-a"
    assert sent[0]["headers"]["X-Correlation-Id"] == "corr-composite"
    assert sent[0]["headers"]["X-Trace-Id"] == "trace-composite"
    assert capture.snapshot_payload["source_response"] == payload
    call = capture.upstream_calls[0].to_create_request()
    assert call.response_hash == selection_for(payload).response_digest
    assert call.response_ref == str(selection_for(payload).calculation_id)
    assert call.supportability_status == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "tenant-b"),
        ("report_type", "portfolio_review"),
        ("portfolio_scope", {"composite_id": "foreign"}),
        ("reporting_currency", "SGD"),
        ("options", {"composite_selection": {"latest": True}}),
    ],
)
async def test_job_scope_conflict_is_refused_before_source_io(field, value):
    class Source:
        async def get_composite_twr(self, *args, **kwargs):
            pytest.fail("scope conflict must precede source I/O")

    job = job_for(calculated_example()).model_copy(update={field: value})
    with pytest.raises(PortfolioReviewInputCaptureError) as caught:
        await CompositeInputProvider(performance_client=Source()).collect_for_job(job)
    assert caught.value.upstream_calls == []
    assert "COMPOSITE_REPORT_" in str(caught.value.original_error)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 202, 409, 503])
async def test_changed_or_failed_response_records_failed_call_not_complete(status):
    payload = calculated_example()
    job = job_for(payload)
    payload["periods"].pop()

    class Source:
        async def get_composite_twr(self, *args, **kwargs):
            return status, payload

    with pytest.raises(PortfolioReviewInputCaptureError) as caught:
        await CompositeInputProvider(performance_client=Source()).collect_for_job(job)
    call = caught.value.upstream_calls[0].to_create_request()
    assert call.status_code == status
    assert call.supportability_status == "error"
    assert call.failure_category == "upstream_error"
    assert call.response_hash is not None


@pytest.mark.asyncio
async def test_timeout_keeps_explicit_unavailable_lineage_without_response():
    class Source:
        async def get_composite_twr(self, *args, **kwargs):
            raise httpx.ReadTimeout("untrusted response text")

    with pytest.raises(PortfolioReviewInputCaptureError) as caught:
        await CompositeInputProvider(performance_client=Source()).collect_for_job(
            job_for(calculated_example())
        )
    call = caught.value.upstream_calls[0].to_create_request()
    assert call.status_code == 504
    assert call.supportability_status == "unavailable"
    assert call.failure_category == "timeout"
    assert call.response_hash is None
    assert "untrusted" not in call.failure_message
