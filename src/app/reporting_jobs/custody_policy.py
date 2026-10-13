"""Report-owned custody invariants; no deployment or downstream authority defaults."""

from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, TypedDict

REQUIRED_POLICY = "retained-render-offer.v1"
ENCODING_VERSION = "json-utf8-compact.v1"
MAX_OFFER_BYTES = 8_388_608
MAX_RETAINED_BYTES = 134_217_728
MAX_TENANT_BYTES = 33_554_432
MAX_RESPONSE_BYTES = 131_072
MAX_DIAGNOSTIC_BYTES = 1_024
RECOVERY_DELAYS = (60, 300, 900)


class CustodyRecoveryError(ValueError):
    """An invariant or authority prerequisite prevented a protected effect."""


class CustodyScope(TypedDict):
    job_id: str
    tenant_id: str
    region: str


class CustodyBindings(Protocol):
    """Explicit future bindings; production currently supplies NONE.

    Implementations must use Platform775 current authenticated action/resource,
    Composite, tenant/region/audience and revocation policy, and readiness must
    verify physical capacity and Root-approved Render/Archive combined bounds.
    Returning from authorize is an authorization, never an actor-header check.
    """

    def require_readiness(self) -> None: ...

    def admit(self, *, report_type: str, tenant_id: str, region: str) -> bool: ...

    def authorize(self, *, operation: str, job_id: str, tenant_id: str, region: str) -> None: ...

    async def verify_final_custody(self, *, identity: dict[str, Any]) -> None: ...

    def transport_headers(
        self, *, operation: str, job_id: str, tenant_id: str, region: str
    ) -> dict[str, str]: ...

    def recovery_action(self, *, response: dict[str, Any]) -> tuple[str, datetime | None]:
        """Bind approved Render contract/due times; no default state-name inference."""
        ...


@dataclass(frozen=True)
class CustodyLease:
    work_item_id: str
    token: str
    epoch: int


current_work_lease: ContextVar[tuple[str, str] | None] = ContextVar(
    "report_work_lease", default=None
)


def encode_render_offer(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if not 0 < len(body) <= MAX_OFFER_BYTES:
        raise CustodyRecoveryError("render_offer_size_exceeded")
    return body


def offer_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise CustodyRecoveryError("custody_naive_time")
    return result.astimezone(UTC)


def custody_protected(row: Any) -> bool:
    """Unknown required-cohort handoff facts protect custody; legacy stays unchanged."""
    if row["custody_policy"] != REQUIRED_POLICY or row["custody_state"] == "verified":
        return False
    return bool(
        row["render_offer_sha256"]
        or row["render_job_id"]
        or row["archive_request_id"]
        or row["custody_initial_count"]
        or row["status"] in {"rendering", "completed", "archiving"}
    )


def requires_work_item(enqueue: bool, custody_policy: str | None) -> bool:
    return enqueue or custody_policy is not None
