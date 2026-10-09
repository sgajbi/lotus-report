"""Read-only exact Manage custody transport; no approval or mutation operations."""

from typing import Any

from app.clients.http_resilience import bounded_read_with_retry
from app.observability import propagation_headers


class ManageClient:
    def __init__(
        self,
        *,
        base_url: str,
        actor_id: str,
        timeout_seconds: float,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.2,
        max_response_bytes: int = 8_388_608,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._actor_id = actor_id
        self._timeout = timeout_seconds
        self._retries = max_retries
        self._backoff = retry_backoff_seconds
        self._max_response_bytes = max_response_bytes

    async def read_eligibility(
        self,
        *,
        endpoint: str,
        binding: dict[str, Any] | None,
        admitted_tenant_id: str,
    ) -> tuple[int, dict[str, Any]]:
        if any(
            not value.strip() or "," in value or "\r" in value or "\n" in value
            for value in (admitted_tenant_id, self._actor_id)
        ):
            raise ValueError("COMPOSITE_MANAGE_READ_IDENTITY_REQUIRED")
        # A configured service actor and fixed read role are never caller-supplied roles.
        headers = {
            **propagation_headers(),
            "X-Tenant-Id": admitted_tenant_id,
            "X-Actor-Id": self._actor_id,
            "X-Role": "REPORT_COMPOSITE_READER",
        }
        if not endpoint.startswith("/api/v1/rebalance/composites/") or "?" in endpoint:
            raise ValueError("COMPOSITE_MANAGE_EXACT_PATH_REQUIRED")
        if binding is not None:
            if not endpoint.endswith("/eligibility-evidence/resolve"):
                raise ValueError("COMPOSITE_MANAGE_READ_ONLY_OPERATION_REQUIRED")
        return await bounded_read_with_retry(
            url=self._base_url + endpoint,
            timeout_seconds=self._timeout,
            headers=headers,
            max_retries=self._retries,
            backoff_seconds=self._backoff,
            json_body=binding,
            max_response_bytes=self._max_response_bytes,
        )
