from typing import Any

import httpx

from app.clients.http_resilience import get_with_retry, post_with_retry, response_payload
from app.observability import propagation_headers


class CoreQueryClient:
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

    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/reporting/portfolio-summary/query"
        headers = self._headers(correlation_id)
        request_payload = dict(payload)
        request_payload["portfolio_id"] = portfolio_id
        return await post_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            json_body=request_payload,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/reporting/asset-allocation/query"
        headers = self._headers(correlation_id)
        request_payload = dict(payload)
        request_payload["scope"] = {"portfolio_id": portfolio_id}
        return await post_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            json_body=request_payload,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )

    async def get_portfolio_transactions(
        self,
        portfolio_id: str,
        params: dict[str, Any],
        correlation_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/portfolios/{portfolio_id}/transactions"
        headers = self._headers(correlation_id)
        return await get_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            params=params,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )

    async def get_portfolio_positions(
        self,
        portfolio_id: str,
        params: dict[str, Any],
        correlation_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/portfolios/{portfolio_id}/positions"
        headers = self._headers(correlation_id)
        return await get_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            params=params,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )

    async def get_portfolio_detail(
        self,
        portfolio_id: str,
        correlation_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/portfolios/{portfolio_id}"
        headers = self._headers(correlation_id)
        return await get_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            params={},
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )

    async def list_portfolios(
        self,
        correlation_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/portfolios/"
        headers = self._headers(correlation_id)
        return await get_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            params={},
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )

    async def get_portfolio_review(
        self,
        portfolio_id: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/portfolios/{portfolio_id}/review"
        headers = self._headers(correlation_id)
        return await post_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            json_body=payload,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )

    def _headers(self, correlation_id: str | None) -> dict[str, str]:
        if not correlation_id:
            return {}
        return propagation_headers(correlation_id)

    def _parse_payload(self, response: httpx.Response) -> dict[str, Any]:
        return response_payload(response)
