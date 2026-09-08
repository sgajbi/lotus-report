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
        *,
        admitted_tenant_id: str,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/reporting/portfolio-summary/query"
        headers = self._headers(correlation_id, admitted_tenant_id)
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
        *,
        admitted_tenant_id: str,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/reporting/asset-allocation/query"
        headers = self._headers(correlation_id, admitted_tenant_id)
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
        *,
        admitted_tenant_id: str,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/portfolios/{portfolio_id}/transactions"
        headers = self._headers(correlation_id, admitted_tenant_id)
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
        *,
        admitted_tenant_id: str,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/portfolios/{portfolio_id}/positions"
        headers = self._headers(correlation_id, admitted_tenant_id)
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
        *,
        admitted_tenant_id: str,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/portfolios/{portfolio_id}"
        headers = self._headers(correlation_id, admitted_tenant_id)
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
        *,
        admitted_tenant_id: str,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/portfolios/{portfolio_id}/review"
        headers = self._headers(correlation_id, admitted_tenant_id)
        return await post_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            json_body=payload,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )

    #: Report sends `X-Tenant-Id` to lotus-ai, lotus-archive and (since #361)
    #: lotus-performance. lotus-core -- the service that actually owns portfolio
    #: ownership -- received it from none of these calls, including the
    #: scheduler's detail reads, so every Core query Report made was
    #: unattributable at the one upstream best placed to refuse a foreign
    #: portfolio.
    #:
    #: The header is built ALWAYS, blank included, rather than omitted when
    #: there is no admitted tenant. Omitting it on blank would make a regression
    #: that stopped threading the value look exactly like a legitimately
    #: tenantless call.
    #:
    #: The correlation branch was a second defect in the same three lines: an
    #: absent correlation id returned `{}` and dropped every propagation header
    #: with it, so a call without correlation was also a call without trace or
    #: request identity. Those are now independent.
    def _headers(self, correlation_id: str | None, admitted_tenant_id: str) -> dict[str, str]:
        headers = propagation_headers(correlation_id) if correlation_id else {}
        headers["X-Tenant-Id"] = admitted_tenant_id
        return headers

    def _parse_payload(self, response: httpx.Response) -> dict[str, Any]:
        return response_payload(response)
