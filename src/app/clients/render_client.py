from typing import Any

from app.clients.http_resilience import post_with_retry


class RenderClient:
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

    async def submit_render_package(
        self,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if correlation_id:
            headers["X-Correlation-ID"] = correlation_id
        if trace_id:
            headers["X-Trace-ID"] = trace_id
            traceparent = _traceparent_header(trace_id)
            if traceparent:
                headers["traceparent"] = traceparent
        return await post_with_retry(
            url=f"{self._base_url}/renders",
            timeout_seconds=self._timeout_seconds,
            json_body=payload,
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
