from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

REPORT_STATUS_EVENT_SCHEMA_VERSION = "report-status-event.v1"
LEGACY_REPORT_STATUS_EVENT_SCHEMA_VERSION = "report-status-event.legacy.v0"

_REQUIRED_PAYLOAD_FIELDS_BY_EVENT_TYPE: dict[str, frozenset[str]] = {
    "job_accepted": frozenset({"report_type"}),
    "job_failed": frozenset({"failure_category", "failure_message"}),
    "job_rendering": frozenset({"render_output_format", "render_template_id"}),
    "job_completed": frozenset({"render_job_id"}),
    "job_archiving": frozenset({"archive_request_id"}),
    "job_archived": frozenset({"archive_document_id"}),
    "job_rerender_requested": frozenset({"snapshot_id"}),
    "job_rerender_archived": frozenset({"archive_document_id"}),
    "job_rerender_failed": frozenset({"failure_message"}),
    "job_regenerate_requested": frozenset({"regenerated_job_id"}),
    "job_regenerate_archived": frozenset({"regenerated_job_id", "archive_document_id"}),
    "job_replay_requested": frozenset({"replayed_job_id"}),
    "job_replay_completed": frozenset({"replayed_job_id", "replayed_status"}),
    "batch_item_replay_requested": frozenset({"batch_item_id", "replayed_job_id"}),
    "batch_item_replay_lineage_bound": frozenset({"source_job_id"}),
}

_SENSITIVE_PAYLOAD_KEYS = frozenset(
    {
        "account_id",
        "account_number",
        "client_id",
        "client_name",
        "holdings",
        "portfolio_id",
        "portfolio_ids",
        "raw_payload",
        "request_body",
        "response_body",
        "secret",
        "token",
        "traceparent",
    }
)


@dataclass(frozen=True)
class ReportStatusEventContract:
    schema_version: str
    event_family: str
    event_payload: dict[str, Any]
    event_idempotency_key: str | None


def report_status_event_family(event_type: str) -> str:
    if event_type.startswith("batch_item_"):
        return "batch_item_replay"
    if event_type.startswith("job_rerender_"):
        return "rerender_lifecycle"
    if event_type.startswith("job_regenerate_"):
        return "regenerate_lifecycle"
    if event_type.startswith("job_replay_"):
        return "replay_lifecycle"
    if event_type in {"job_rendering", "job_completed"}:
        return "render_lifecycle"
    if event_type in {"job_archiving", "job_archived"}:
        return "archive_lifecycle"
    return "job_lifecycle"


def build_report_status_event_contract(
    *,
    event_type: str,
    from_status: str | None,
    to_status: str,
    event_payload: Mapping[str, Any] | None = None,
    event_idempotency_key: str | None = None,
) -> ReportStatusEventContract:
    payload = dict(event_payload or {})
    payload.setdefault("from_status", from_status)
    payload.setdefault("to_status", to_status)
    payload.setdefault("event_type", event_type)

    _validate_support_safe_payload_keys(payload)
    missing = {
        field
        for field in _REQUIRED_PAYLOAD_FIELDS_BY_EVENT_TYPE.get(event_type, frozenset())
        if not payload.get(field)
    }
    if missing:
        raise ValueError(
            "report_status_event_payload_missing:" + ",".join(sorted(missing)) + f":{event_type}"
        )

    return ReportStatusEventContract(
        schema_version=REPORT_STATUS_EVENT_SCHEMA_VERSION,
        event_family=report_status_event_family(event_type),
        event_payload=payload,
        event_idempotency_key=_normalized_event_idempotency_key(event_idempotency_key),
    )


def legacy_report_status_event_contract(
    *,
    event_type: str,
    from_status: str | None,
    to_status: str,
) -> ReportStatusEventContract:
    return ReportStatusEventContract(
        schema_version=LEGACY_REPORT_STATUS_EVENT_SCHEMA_VERSION,
        event_family=report_status_event_family(event_type),
        event_payload={
            "event_type": event_type,
            "from_status": from_status,
            "to_status": to_status,
            "payload_posture": "legacy_message_only",
        },
        event_idempotency_key=None,
    )


def _validate_support_safe_payload_keys(payload: Mapping[str, Any]) -> None:
    found = sorted(set(payload) & _SENSITIVE_PAYLOAD_KEYS)
    if found:
        raise ValueError("report_status_event_payload_sensitive_keys:" + ",".join(found))


def _normalized_event_idempotency_key(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    return value.strip()
