"""Native bounded GET transport against controlled responses; not Archive197 certification."""

import json

import httpx
import pytest

from app.clients.archive_client import ArchiveClient
from app.clients.http_resilience import bounded_read_with_retry
from app.reporting_jobs.models import ReportCallerContext


def controlled_transport(monkeypatch, handler):
    native = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: native(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.asyncio
async def test_exact_privileged_get_preserves_body_bytes_and_native_scope(monkeypatch):
    calls = []
    raw = b'{ "transition_reason" : "controlled \\u00e9", "requested_by": "operator" }'

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=raw, headers={"Cache-Control": "no-store"})

    controlled_transport(monkeypatch, handler)
    caller = ReportCallerContext(
        triggered_by="actor",
        caller_application="lotus-gateway",
        tenant_id="tenant",
        region="APAC",
        correlation_id="correlation",
        trace_id="0123456789abcdef0123456789abcdef",
    )
    result = await ArchiveClient(
        base_url="http://archive", timeout_seconds=1, max_retries=0, retry_backoff_seconds=0
    ).get_notice_relationship("doc-source", "life-exact", caller=caller)
    assert result.response_bytes == raw
    assert result.payload == json.loads(raw)
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "GET"
    assert request.url.path == "/documents/doc-source/lifecycle-relationships/life-exact"
    assert request.headers["X-Caller-Service"] == "lotus-report"
    assert request.headers["X-Actor-Type"] == "service"
    assert request.headers["X-Tenant-Id"] == "tenant"
    assert request.headers["X-Region"] == "APAC"
    assert "X-Capabilities" not in request.headers


@pytest.mark.asyncio
async def test_exact_raw_byte_bound_accepts_boundary_and_refuses_overflow_before_observer(
    monkeypatch,
):
    raw = b'{ "source" : "exact" }'
    controlled_transport(monkeypatch, lambda request: httpx.Response(200, content=raw))
    observed = []
    arguments = dict(
        url="http://archive/documents/doc",
        timeout_seconds=1,
        headers={},
        json_body=None,
        max_retries=0,
        response_bytes_observer=observed.append,
    )
    status, _ = await bounded_read_with_retry(**arguments, max_response_bytes=len(raw))
    assert status == 200
    assert observed == [raw]
    observed.clear()
    with pytest.raises(ValueError, match="BYTE_BOUND_EXCEEDED"):
        await bounded_read_with_retry(**arguments, max_response_bytes=len(raw) - 1)
    assert observed == []


@pytest.mark.asyncio
async def test_missing_scope_cannot_issue_privileged_read(monkeypatch):
    calls = []
    controlled_transport(monkeypatch, lambda request: calls.append(request))
    caller = ReportCallerContext(
        triggered_by="actor",
        caller_application="test",
        tenant_id="",
        region="APAC",
        correlation_id="correlation",
        trace_id="trace",
    )
    with pytest.raises(ValueError, match="ARCHIVE_READ_SCOPE_REQUIRED"):
        await ArchiveClient(
            base_url="http://archive", timeout_seconds=1, max_retries=0, retry_backoff_seconds=0
        ).get_notice_document("doc", caller=caller)
    assert calls == []
