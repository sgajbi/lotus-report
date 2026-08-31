"""Client for the lotus-ai accepted workflow-pack output projection.

lotus-report reads exactly two lotus-ai surfaces: the review-gated,
tenant-scoped accepted-output projection by run id (`advisor_brief.pack@v1`),
and the latest-accepted identity lookup that answers pre-order availability
(lotus-ai#183). Report composes that source-owned truth into the
ADVISOR_COMMENTARY report section and its ordering availability, and never
regenerates, edits, or re-reviews narrative content.
"""

from typing import Any

from app.clients.http_resilience import get_with_retry
from app.observability import propagation_headers

REPORT_CALLER_APP = "lotus-report"


class AiClient:
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

    async def get_latest_accepted_brief(
        self,
        *,
        portfolio_id: str,
        tenant_id: str,
        as_of_date: str | None = None,
        reporting_currency: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/platform/workflow-packs/runs/accepted-latest"
        params: dict[str, str] = {
            "pack_family": "advisor_brief",
            "portfolio_id": portfolio_id,
        }
        if as_of_date:
            params["as_of_date"] = as_of_date
        if reporting_currency:
            params["reporting_currency"] = reporting_currency
        headers = {
            **propagation_headers(),
            "X-Caller-App": REPORT_CALLER_APP,
            "X-Tenant-Id": tenant_id,
        }
        return await get_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            params=params,
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )

    async def get_accepted_workflow_output(
        self,
        run_id: str,
        *,
        tenant_id: str,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base_url}/platform/workflow-packs/runs/{run_id}/accepted-output"
        headers = {
            **propagation_headers(),
            "X-Caller-App": REPORT_CALLER_APP,
            "X-Tenant-Id": tenant_id,
        }
        return await get_with_retry(
            url=url,
            timeout_seconds=self._timeout_seconds,
            params={},
            headers=headers,
            max_retries=self._max_retries,
            backoff_seconds=self._retry_backoff_seconds,
        )
