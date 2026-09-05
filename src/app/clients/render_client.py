from typing import Any

from app.clients.http_resilience import get_with_retry, post_with_retry


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
        result: tuple[int, dict[str, Any]] = await post_with_retry(
            url=f"{self._base_url}/renders",
            timeout_seconds=self._timeout_seconds,
            json_body=payload,
            headers=_request_headers(
                correlation_id=correlation_id,
                trace_id=trace_id,
                content_type="application/json",
            ),
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )
        return result

    async def get_render_status(
        self,
        render_job_id: str,
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """GET /renders/{render_job_id} - the persisted render job's posture:
        status, template identity, artifact hash metadata, and the archive
        custody outcome when available; governed 404 for an unknown id. Used
        to RESOLVE an in-flight render before any resubmission, so a package
        rebuilt by newer code never collides with the one the id already
        carries."""

        result: tuple[int, dict[str, Any]] = await get_with_retry(
            url=f"{self._base_url}/renders/{render_job_id}",
            timeout_seconds=self._timeout_seconds,
            params={},
            headers=_request_headers(correlation_id=correlation_id, trace_id=trace_id),
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )
        return result

    async def get_render_diagnostics(
        self,
        render_job_id: str,
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """GET /renders/{render_job_id}/diagnostics - Render's stale-work
        escalation channel (report#303): recovery_action, retryable,
        stale_state, and a support message, computed against the OWNER's
        staleness thresholds. Report maps the owner vocabulary through an
        explicit table and fails closed on unmapped values."""

        result: tuple[int, dict[str, Any]] = await get_with_retry(
            url=f"{self._base_url}/renders/{render_job_id}/diagnostics",
            timeout_seconds=self._timeout_seconds,
            params={},
            headers=_request_headers(correlation_id=correlation_id, trace_id=trace_id),
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )
        return result

    async def get_template_projection(
        self,
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """GET /system/templates - the registry projection for version-aware
        family supportability (render#265): per registered version, its id,
        version, renderable status, publication posture, and supported report
        types/contract versions. Deliberately narrow by contract - digests,
        locales, output formats, and runtime posture are other surfaces."""

        result: tuple[int, dict[str, Any]] = await get_with_retry(
            url=f"{self._base_url}/system/templates",
            timeout_seconds=self._timeout_seconds,
            params={},
            headers=_request_headers(correlation_id=correlation_id, trace_id=trace_id),
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )
        return result

    async def get_metadata(
        self,
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        result: tuple[int, dict[str, Any]] = await get_with_retry(
            url=f"{self._base_url}/metadata",
            timeout_seconds=self._timeout_seconds,
            params={},
            headers=_request_headers(correlation_id=correlation_id, trace_id=trace_id),
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )
        return result


def _request_headers(
    *,
    correlation_id: str | None,
    trace_id: str | None,
    content_type: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = content_type
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id
    if trace_id:
        headers["X-Trace-ID"] = trace_id
        traceparent = _traceparent_header(trace_id)
        if traceparent:
            headers["traceparent"] = traceparent
    return headers


def _traceparent_header(trace_id: str) -> str | None:
    if len(trace_id) != 32:
        return None
    try:
        int(trace_id, 16)
    except ValueError:
        return None
    return f"00-{trace_id}-0000000000000001-01"
