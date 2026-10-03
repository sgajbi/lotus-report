"""Shared enterprise audit envelope and recursive redaction for log producers/sinks."""

import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

_REDACT_FIELDS = {
    "password",
    "secret",
    "token",
    "authorization",
    "ssn",
    "account_number",
    "client_email",
}


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if str(key).lower() in _REDACT_FIELDS else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    return value


class _EnterpriseAuditEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["lotus-report.audit.v1"] = "lotus-report.audit.v1"
    service: Literal["lotus-report"]
    action: str = Field(strict=True, min_length=1, max_length=1024)
    actor_id: str | None = Field(strict=True, max_length=256)
    tenant_id: str | None = Field(strict=True, max_length=256)
    role: str | None = Field(strict=True, max_length=256)
    correlation_id: str | None = Field(strict=True, max_length=256)
    timestamp_utc: AwareDatetime
    policy_version: str = Field(strict=True, min_length=1, max_length=64)
    metadata: dict[str, Any]

    @field_validator("timestamp_utc")
    @classmethod
    def _utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


def validated_audit_envelope(value: Any) -> dict[str, Any] | None:
    """Malformed/non-JSON audit input gets a fixed refusal rather than raw output."""

    try:
        envelope = _EnterpriseAuditEnvelope.model_validate(value)
        metadata = redact_sensitive(envelope.metadata)
        if len(json.dumps(metadata, allow_nan=False).encode("utf-8")) > 8192:
            return None
        result = envelope.model_dump(mode="json", exclude={"metadata"})
        result["metadata"] = metadata
        return result
    except (TypeError, ValueError, RecursionError):
        return None
