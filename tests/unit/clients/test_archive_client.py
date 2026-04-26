import pytest

from app.clients import archive_client as archive_client_module
from app.clients.archive_client import ArchiveClient


@pytest.mark.asyncio
async def test_archive_document_posts_to_archive_endpoint(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_post_with_retry(**kwargs):
        captured.update(kwargs)
        return 201, {"document_id": "doc_123"}

    monkeypatch.setattr(archive_client_module, "post_with_retry", _fake_post_with_retry)
    client = ArchiveClient(
        base_url="http://archive.dev.lotus/",
        timeout_seconds=12.5,
        max_retries=4,
        retry_backoff_seconds=0.75,
    )

    status_code, payload = await client.archive_document(
        {"archive_request_id": "arch_123"},
        actor_id="advisor-123",
        tenant_id="tenant-sg",
        region="APAC",
        correlation_id="corr-123",
        trace_id="0123456789abcdef0123456789abcdef",
        booking_center_code="SG",
        role="advisor",
    )

    assert status_code == 201
    assert payload == {"document_id": "doc_123"}
    assert captured == {
        "url": "http://archive.dev.lotus/documents",
        "timeout_seconds": 12.5,
        "json_body": {"archive_request_id": "arch_123"},
        "headers": {
            "Content-Type": "application/json",
            "X-Caller-Service": "lotus-report",
            "X-Actor-Type": "service",
            "X-Actor-Id": "advisor-123",
            "X-Caller-Application": "lotus-report",
            "X-Tenant-Id": "tenant-sg",
            "X-Region": "APAC",
            "X-Correlation-ID": "corr-123",
            "X-Trace-ID": "0123456789abcdef0123456789abcdef",
            "traceparent": "00-0123456789abcdef0123456789abcdef-0000000000000001-01",
            "X-Booking-Center-Code": "SG",
            "X-Role": "advisor",
        },
        "max_retries": 4,
        "backoff_seconds": 0.75,
    }


@pytest.mark.asyncio
async def test_archive_document_omits_optional_headers_when_absent(monkeypatch):
    captured_headers: dict[str, str] = {}

    async def _fake_post_with_retry(**kwargs):
        captured_headers.update(kwargs["headers"])
        return 200, {}

    monkeypatch.setattr(archive_client_module, "post_with_retry", _fake_post_with_retry)
    client = ArchiveClient(
        base_url="http://archive.dev.lotus",
        timeout_seconds=5.0,
        max_retries=1,
        retry_backoff_seconds=0.1,
    )

    await client.archive_document(
        {"archive_request_id": "arch_456"},
        actor_id="advisor-456",
        tenant_id="tenant-us",
        region="AMER",
        correlation_id="corr-456",
        trace_id="trace-456",
    )

    assert captured_headers == {
        "Content-Type": "application/json",
        "X-Caller-Service": "lotus-report",
        "X-Actor-Type": "service",
        "X-Actor-Id": "advisor-456",
        "X-Caller-Application": "lotus-report",
        "X-Tenant-Id": "tenant-us",
        "X-Region": "AMER",
        "X-Correlation-ID": "corr-456",
        "X-Trace-ID": "trace-456",
    }


@pytest.mark.asyncio
async def test_archive_document_sends_trace_without_invalid_traceparent(monkeypatch):
    captured_headers: dict[str, str] = {}

    async def _fake_post_with_retry(**kwargs):
        captured_headers.update(kwargs["headers"])
        return 200, {}

    monkeypatch.setattr(archive_client_module, "post_with_retry", _fake_post_with_retry)
    client = ArchiveClient(
        base_url="http://archive.dev.lotus",
        timeout_seconds=5.0,
        max_retries=1,
        retry_backoff_seconds=0.1,
    )

    await client.archive_document(
        {"archive_request_id": "arch_789"},
        actor_id="advisor-789",
        tenant_id="tenant-eu",
        region="EMEA",
        correlation_id="corr-789",
        trace_id="trace-archive",
    )

    assert captured_headers == {
        "Content-Type": "application/json",
        "X-Caller-Service": "lotus-report",
        "X-Actor-Type": "service",
        "X-Actor-Id": "advisor-789",
        "X-Caller-Application": "lotus-report",
        "X-Tenant-Id": "tenant-eu",
        "X-Region": "EMEA",
        "X-Correlation-ID": "corr-789",
        "X-Trace-ID": "trace-archive",
    }
