import asyncio
import time
from collections.abc import Awaitable, Callable
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
        poll_budget_seconds: float = 10.0,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        #: Report's own bounded deadline for one async result. The source
        #: states when it may next be polled; Report states how long it will
        #: wait overall. Both bounds hold - neither is traded for the other.
        self._poll_budget_seconds = poll_budget_seconds
        #: Injectable so budget semantics are proven with a fake clock and
        #: sleeper rather than wall-clock sleeps.
        self._clock = clock or time.monotonic
        self._sleeper = sleeper

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

        Two authorities, both honoured:

        - The SOURCE states the minimum time before its result may next be
          polled - the Retry-After header, or recommended_poll_after_seconds
          in the accepted envelope. That is an instruction, not a suggestion:
          this loop never polls early and never shortens a stated wait. A
          source stating nothing keeps the old shape - immediate first poll,
          then the linear fallback.
        - REPORT states how long a capture waits overall
          (poll_budget_seconds), plus an attempt bound as a backstop. When
          the source's stated wait ends after Report's remaining budget, the
          loop stops immediately and returns the accepted envelope: polling
          early would disobey the source, sleeping on would disobey the
          budget, and the truthful outcome is the pending posture.

        Exhaustion of either bound returns the LAST payload - for a
        still-pending result that is the accepted envelope, which is the
        capture's evidence for its accepted-but-not-complete posture. The
        loop never raises on pending: what a pending result means for a
        report is the capture's judgement, not the transport's.
        """

        sleeper = self._sleeper or asyncio.sleep
        result_url = f"{self._base_url}{result_path}"
        deadline = self._clock() + self._poll_budget_seconds
        last_status = fallback_status_code
        last_payload = fallback_payload
        delay = self._stated_poll_delay(fallback_payload, header_value=None)
        for attempt in range(self._max_retries + 8):
            wait = float(delay) if delay is not None else self._retry_backoff_seconds * attempt
            if wait > 0.0:
                if self._clock() + wait > deadline:
                    return last_status, last_payload
                await sleeper(wait)
            elif self._clock() >= deadline:
                return last_status, last_payload
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(result_url, params={}, headers=headers)
            last_status = response.status_code
            last_payload = response_payload(response)
            if last_status >= 400 or "results_by_period" in last_payload:
                return last_status, last_payload
            delay = self._stated_poll_delay(
                last_payload, header_value=response.headers.get("Retry-After")
            )
        return last_status, last_payload

    def _stated_poll_delay(
        self, payload: dict[str, Any], *, header_value: str | None
    ) -> int | None:
        """The source's stated minimum wait, in whole seconds.

        Retry-After is delta-seconds and the envelope schema declares an
        integer; parsing as int also keeps a duration out of the
        monetary-float guard's vocabulary. An unparseable header is ignored
        (one bad header must not fail a poll that would have succeeded);
        whether a large stated wait fits is the BUDGET's decision, made in
        the loop - it is never truncated here.
        """

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
        return stated

    def _parse_payload(self, response: httpx.Response) -> dict[str, Any]:
        return response_payload(response)
