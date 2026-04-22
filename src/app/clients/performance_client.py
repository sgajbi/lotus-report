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

    async def _poll_workspace_summary_result(
        self,
        *,
        result_path: str,
        headers: dict[str, str],
        fallback_status_code: int,
        fallback_payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        result_url = f"{self._base_url}{result_path}"
        last_status = fallback_status_code
        last_payload = fallback_payload
        for attempt in range(self._max_retries + 8):
            if attempt:
                await asyncio.sleep(self._retry_backoff_seconds * attempt)
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(result_url, params={}, headers=headers)
            last_status = response.status_code
            last_payload = response_payload(response)
            if last_status >= 400 or "results_by_period" in last_payload:
                return last_status, last_payload
        return last_status, last_payload

    async def _poll_contribution_result(
        self,
        *,
        result_path: str,
        headers: dict[str, str],
        fallback_status_code: int,
        fallback_payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        result_url = f"{self._base_url}{result_path}"
        last_status = fallback_status_code
        last_payload = fallback_payload
        for attempt in range(self._max_retries + 8):
            if attempt:
                await asyncio.sleep(self._retry_backoff_seconds * attempt)
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(result_url, params={}, headers=headers)
            last_status = response.status_code
            last_payload = response_payload(response)
            if last_status >= 400 or "results_by_period" in last_payload:
                return last_status, last_payload
        return last_status, last_payload

    def _parse_payload(self, response: httpx.Response) -> dict[str, Any]:
        return response_payload(response)
