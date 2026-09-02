import asyncio
from typing import Any

import httpx

from app.clients.http_resilience import post_with_retry, response_payload
from app.observability import propagation_headers


class PerformanceClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.2,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    async def get_workspace_summary(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/performance/workspace-summary"
        headers = propagation_headers()
        status_code, response = await post_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            json_body=payload,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )
        if status_code >= 400 or "results_by_period" in response:
            return status_code, response
        result_path = response.get("result_path")
        if not isinstance(result_path, str) or not result_path:
            return status_code, response
        return await self._poll_workspace_summary_result(
            result_path=result_path,
            headers=headers,
            fallback_status_code=status_code,
            fallback_payload=response,
        )

    async def get_contribution(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/performance/contribution"
        headers = propagation_headers()
        status_code, response = await post_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            json_body=payload,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )
        if status_code >= 400 or "results_by_period" in response:
            return status_code, response
        result_path = response.get("result_path")
        if not isinstance(result_path, str) or not result_path:
            return status_code, response
        return await self._poll_contribution_result(
            result_path=result_path,
            headers=headers,
            fallback_status_code=status_code,
            fallback_payload=response,
        )

    async def get_attribution(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Brinson attribution for the report window (issue #254).

        Same submit-then-poll shape as its siblings: a 200 carries
        `results_by_period`; a 202 carries `result_path`, and polling is
        bounded. A response that is still the accepted envelope after the
        budget is returned AS the accepted envelope - the capture states an
        accepted-but-not-complete posture from it rather than this client
        deciding to fail or to wait forever.
        """

        url = f"{self._base_url}/performance/attribution"
        headers = propagation_headers()
        status_code, response = await post_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            json_body=payload,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )
        if status_code >= 400 or "results_by_period" in response:
            return status_code, response
        result_path = response.get("result_path")
        if not isinstance(result_path, str) or not result_path:
            return status_code, response
        return await self._poll_analytics_result(
            result_path=result_path,
            headers=headers,
            fallback_status_code=status_code,
            fallback_payload=response,
        )

    async def _poll_workspace_summary_result(
        self,
        *,
        result_path: str,
        headers: dict[str, str],
        fallback_status_code: int,
        fallback_payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        return await self._poll_analytics_result(
            result_path=result_path,
            headers=headers,
            fallback_status_code=fallback_status_code,
            fallback_payload=fallback_payload,
        )

    async def _poll_contribution_result(
        self,
        *,
        result_path: str,
        headers: dict[str, str],
        fallback_status_code: int,
        fallback_payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        return await self._poll_analytics_result(
            result_path=result_path,
            headers=headers,
            fallback_status_code=fallback_status_code,
            fallback_payload=fallback_payload,
        )

    async def _poll_analytics_result(
        self,
        *,
        result_path: str,
        headers: dict[str, str],
        fallback_status_code: int,
        fallback_payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """One poll loop for every async analytics result.

        This existed as byte-identical copies per endpoint; a third copy for
        attribution would have been the two-copies defect at n=3, so the
        copies now delegate here.

        The wait honours the cadence the source states - the Retry-After
        header, or `recommended_poll_after_seconds` in the accepted envelope -
        falling back to the linear schedule the copies used. A source that
        says when to come back should not be hammered on our schedule. Each
        wait is capped so a misbehaving source stating an hour cannot hang a
        capture; the attempt budget bounds the total either way.

        Exhaustion returns the LAST payload - for a still-pending result that
        is the accepted envelope, which is the capture's evidence for its
        accepted-but-not-complete posture. This loop never raises on pending:
        deciding what a pending result means for a report is the capture's
        judgement, not the transport's.
        """

        result_url = f"{self._base_url}{result_path}"
        last_status = fallback_status_code
        last_payload = fallback_payload
        delay = self._stated_poll_delay(fallback_payload, header_value=None) or 0.0
        for attempt in range(self._max_retries + 8):
            # The stated wait applies BEFORE the first poll too - the source
            # says "wait at least N between polls", and the 202 that carried
            # the cadence counts as the previous contact. A source stating
            # nothing keeps the old shape: first poll immediate, then the
            # linear fallback.
            wait = delay or (self._retry_backoff_seconds * attempt if attempt else 0.0)
            if wait:
                await asyncio.sleep(wait)
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(result_url, params={}, headers=headers)
            last_status = response.status_code
            last_payload = response_payload(response)
            if last_status >= 400 or "results_by_period" in last_payload:
                return last_status, last_payload
            delay = (
                self._stated_poll_delay(
                    last_payload, header_value=response.headers.get("Retry-After")
                )
                or 0.0
            )
        return last_status, last_payload

    #: Upper bound on a single stated wait. Bounds a misbehaving source; the
    #: attempt budget bounds the total.
    _MAX_STATED_POLL_DELAY_SECONDS = 5.0

    def _stated_poll_delay(
        self, payload: dict[str, Any], *, header_value: str | None
    ) -> float | None:
        # Whole seconds by contract: Retry-After is delta-seconds and the
        # envelope's schema declares an integer. Parsing as int also keeps
        # this duration out of the monetary-float guard's vocabulary.
        stated: int | None = None
        if header_value is not None:
            try:
                stated = int(header_value)
            except ValueError:
                stated = None
        if stated is None:
            recommended = payload.get("recommended_poll_after_seconds")
            if isinstance(recommended, int) and not isinstance(recommended, bool):
                stated = recommended
        if stated is None or stated <= 0:
            return None
        return min(stated, self._MAX_STATED_POLL_DELAY_SECONDS)

    def _parse_payload(self, response: httpx.Response) -> dict[str, Any]:
        return response_payload(response)
