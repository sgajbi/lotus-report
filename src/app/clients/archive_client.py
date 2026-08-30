from typing import Any

from app.clients.http_resilience import get_with_retry, post_with_retry


class ArchiveClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        retry_backoff_seconds: float,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    async def archive_document(
        self,
        payload: dict[str, Any],
        *,
        actor_id: str,
        tenant_id: str,
        region: str,
        correlation_id: str,
        trace_id: str,
        booking_center_code: str | None = None,
        role: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        headers = {
            "Content-Type": "application/json",
            "X-Caller-Service": "lotus-report",
            "X-Actor-Type": "service",
            "X-Actor-Id": actor_id,
            "X-Caller-Application": "lotus-report",
            "X-Tenant-Id": tenant_id,
            "X-Region": region,
            "X-Correlation-ID": correlation_id,
            "X-Trace-ID": trace_id,
        }
        traceparent = _traceparent_header(trace_id)
        if traceparent:
            headers["traceparent"] = traceparent
        if booking_center_code:
            headers["X-Booking-Center-Code"] = booking_center_code
        if role:
            headers["X-Role"] = role
        return await post_with_retry(
            url=f"{self._base_url}/documents",
            timeout_seconds=self._timeout_seconds,
            json_body=payload,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )

    async def get_document_by_request_id(
        self,
        archive_request_id: str,
        *,
        actor_id: str,
        tenant_id: str,
        region: str,
        correlation_id: str,
        trace_id: str,
        booking_center_code: str | None = None,
        role: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        headers = {
            "X-Caller-Service": "lotus-report",
            "X-Actor-Type": "service",
            "X-Actor-Id": actor_id,
            "X-Caller-Application": "lotus-report",
            "X-Tenant-Id": tenant_id,
            "X-Region": region,
            "X-Correlation-ID": correlation_id,
            "X-Trace-ID": trace_id,
        }
        if booking_center_code:
            headers["X-Booking-Center-Code"] = booking_center_code
        if role:
            headers["X-Role"] = role
        traceparent = _traceparent_header(trace_id)
        if traceparent:
            headers["traceparent"] = traceparent
        return await get_with_retry(
            url=f"{self._base_url}/documents/by-request-id/{archive_request_id}",
            timeout_seconds=self._timeout_seconds,
            params={},
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )


def _traceparent_header(trace_id: str) -> str | None:
    if len(trace_id) != 32:
        return None
    try:
        int(trace_id, 16)
    except ValueError:
        return None
    return f"00-{trace_id}-0000000000000001-01"
