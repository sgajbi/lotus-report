from typing import Any

from app.clients.http_resilience import post_with_retry
from app.observability import propagation_headers


#: Same contract as `performance_client` (#361), same defect class (#375):
#: every analytics call Report makes here is `input_mode: "stateful"`, which
#: lotus-risk's receiving contract (risk#297) binds to a REQUIRED admitted
#: tenant - missing or blank refuses with 401 MISSING_TENANT_AUTHORITY before
#: any upstream I/O once that lands, and until then the row lotus-risk records
#: is unattributable in principle because the value was never transmitted.
#:
#: The header is sent ALWAYS, blank included, rather than omitted when there
#: is no admitted tenant: omitting on blank would make code that stopped
#: threading the value look exactly like a legitimately tenantless call.
def _tenant_headers(admitted_tenant_id: str) -> dict[str, str]:
    return {**propagation_headers(), "X-Tenant-Id": admitted_tenant_id}


class RiskClient:
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

    async def rolling_metrics(
        self, payload: dict[str, Any], *, admitted_tenant_id: str
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/analytics/risk/rolling-metrics"
        headers = _tenant_headers(admitted_tenant_id)
        return await post_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            json_body=payload,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )

    async def historical_attribution(
        self, payload: dict[str, Any], *, admitted_tenant_id: str
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/analytics/risk/historical-attribution"
        headers = _tenant_headers(admitted_tenant_id)
        return await post_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            json_body=payload,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )

    async def drawdown_analytics(
        self, payload: dict[str, Any], *, admitted_tenant_id: str
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/analytics/risk/drawdown"
        headers = _tenant_headers(admitted_tenant_id)
        return await post_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            json_body=payload,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )

    async def calculate_risk(
        self, payload: dict[str, Any], *, admitted_tenant_id: str
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/analytics/risk/calculate"
        headers = _tenant_headers(admitted_tenant_id)
        return await post_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            json_body=payload,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )
