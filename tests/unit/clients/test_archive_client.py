import pytest

from app.clients import archive_client as archive_client_module
from app.clients.archive_client import ArchiveClient


@pytest.mark.asyncio
async def test_get_document_omits_traceparent_when_trace_id_is_invalid_hex(monkeypatch):
    """The traceparent header carries only a valid W3C trace id; an opaque
    32-char non-hex trace id still travels as X-Trace-ID but never as a
    malformed traceparent."""

    captured_headers: dict[str, str] = {}

    async def _fake_get_with_retry(**kwargs):
        captured_headers.update(kwargs["headers"])
        return 200, {}

    monkeypatch.setattr(archive_client_module, "get_with_retry", _fake_get_with_retry)
    client = ArchiveClient(
        base_url="http://archive.dev.lotus",
        timeout_seconds=5.0,
        max_retries=1,
        retry_backoff_seconds=0.1,
    )

    await client.get_document_by_request_id(
        "areq_hex",
        actor_id="advisor-hex",
        tenant_id="tenant-eu",
        region="EMEA",
        correlation_id="corr-hex",
        trace_id="zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",  # 32 chars, invalid hex
    )

    assert "traceparent" not in captured_headers
    assert captured_headers["X-Trace-ID"] == "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"


@pytest.mark.asyncio
async def test_get_document_by_request_id_calls_lookup_endpoint(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_get_with_retry(**kwargs):
        captured.update(kwargs)
        return 200, {"document_id": "doc_123", "archive_request_id": "arch_rdr_1_pdf"}

    monkeypatch.setattr(archive_client_module, "get_with_retry", _fake_get_with_retry)
    client = ArchiveClient(
        base_url="http://archive.dev.lotus/",
        timeout_seconds=12.5,
        max_retries=4,
        retry_backoff_seconds=0.75,
    )

    status_code, payload = await client.get_document_by_request_id(
        "arch_rdr_1_pdf",
        actor_id="advisor-123",
        tenant_id="tenant-sg",
        region="APAC",
        correlation_id="corr-123",
        trace_id="0123456789abcdef0123456789abcdef",
        booking_center_code="SG",
        role="advisor",
    )

    assert status_code == 200
    assert payload["document_id"] == "doc_123"
    assert captured["url"] == ("http://archive.dev.lotus/documents/by-request-id/arch_rdr_1_pdf")
    headers = captured["headers"]
    assert headers["X-Tenant-Id"] == "tenant-sg"
    assert headers["X-Booking-Center-Code"] == "SG"
    assert headers["X-Role"] == "advisor"
    assert headers["traceparent"].startswith("00-0123456789abcdef0123456789abcdef-")
    assert captured["max_retries"] == 4
    assert captured["backoff_seconds"] == 0.75


@pytest.mark.asyncio
async def test_get_document_by_request_id_omits_optional_headers(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_get_with_retry(**kwargs):
        captured.update(kwargs)
        return 404, {"error": {"code": "document_not_found"}}

    monkeypatch.setattr(archive_client_module, "get_with_retry", _fake_get_with_retry)
    client = ArchiveClient(
        base_url="http://archive.dev.lotus",
        timeout_seconds=5.0,
        max_retries=2,
        retry_backoff_seconds=0.2,
    )

    status_code, _payload = await client.get_document_by_request_id(
        "arch_missing",
        actor_id="system-worker",
        tenant_id="tenant-sg",
        region="APAC",
        correlation_id="corr-404",
        trace_id="not-a-hex-trace",
    )

    assert status_code == 404
    headers = captured["headers"]
    assert "X-Booking-Center-Code" not in headers
    assert "X-Role" not in headers
    assert "traceparent" not in headers
