"""Exact retained calculated dataset admission, without financial recomputation."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any

from pydantic import ValidationError

from app.composite_reporting.models import (
    CompositeCalculatedResponse,
    CompositePeriod,
    CompositeReportSelection,
    CompositeWindowPin,
)
from app.reporting_lineage.store import canonical_json_dumps


class CompositeEvidenceRefused(ValueError):
    """Bounded refusal code; never projects supplier payload or raw error text."""


def response_digest(payload: dict[str, Any]) -> str:
    return "sha256:" + sha256(canonical_json_dumps(payload).encode("utf-8")).hexdigest()


def admit_composite_response(
    *,
    selection: CompositeReportSelection,
    admitted_tenant_id: str,
    status_code: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if admitted_tenant_id != selection.tenant_id or not admitted_tenant_id.strip():
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_TENANT_MISMATCH")
    if status_code != 200:
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_SOURCE_UNAVAILABLE")
    try:
        actual_digest = response_digest(payload)
        response = CompositeCalculatedResponse.model_validate(payload)
    except (ValidationError, ValueError, TypeError) as exc:
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_SOURCE_INVALID") from exc
    if actual_digest != selection.response_digest:
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_RESPONSE_CHANGED")
    _require_series_identity(selection, response)
    if len(response.periods) != len(selection.windows):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_PERIOD_MISSING")
    for period, window in zip(response.periods, selection.windows, strict=True):
        _require_period_identity(selection, period, window)
    if response.cumulative_return != response.periods[-1].cumulative_return:
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_CUMULATIVE_CONFLICT")
    if response.status == "BLOCKED" and response.cumulative_return is not None:
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_BLOCKED_VALUE")
    if response.status == "READY" and (
        response.cumulative_return is None
        or any(item.status != "READY" for item in response.periods)
    ):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_READY_EVIDENCE_MISSING")
    return {
        "contract_version": "composite_review.v1",
        "qualification": "EXPLICIT_RETAINED_CALCULATED_REPLAY",
        "publication_state": "NOT_ATTESTED",
        "tenant_id": admitted_tenant_id,
        "selection": selection.model_dump(mode="json"),
        "source_response_digest": actual_digest,
        "source_response": deepcopy(payload),
    }


def _require_series_identity(
    selection: CompositeReportSelection, response: CompositeCalculatedResponse
) -> None:
    manifest = response.selection_manifest
    if (
        response.calculation_id != selection.calculation_id
        or response.composite_id != selection.composite_id
        or response.period_start != selection.period_start
        or response.period_end != selection.period_end
        or response.methodology != selection.methodology
        or manifest.engine_version != selection.engine_version
        or manifest.calculation_fingerprint != selection.calculation_fingerprint
        or manifest.windows != selection.windows
    ):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_IDENTITY_MISMATCH")


def _require_period_identity(
    selection: CompositeReportSelection, period: CompositePeriod, window: CompositeWindowPin
) -> None:
    if (
        (period.period_start, period.period_end) != (window.period_start, window.period_end)
        or period.restatement_sequence != window.restatement_sequence
        or period.return_view not in (None, selection.return_view)
        or period.reporting_currency not in (None, selection.reporting_currency)
    ):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_PERIOD_IDENTITY_MISMATCH")
    _require_period_financial_posture(period)
    members = period.member_contributions
    if len(members) != period.member_count or len({item.portfolio_id for item in members}) != len(
        members
    ):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_MEMBER_COUNT_MISMATCH")
    for member in members:
        if (
            (member.period_start, member.period_end) != (period.period_start, period.period_end)
            or member.restatement_sequence != period.restatement_sequence
            or member.source_fingerprint not in period.source_fingerprints
            or member.restatement_version not in period.restatement_versions
        ):
            raise CompositeEvidenceRefused("COMPOSITE_REPORT_MEMBER_IDENTITY_MISMATCH")


def _require_period_financial_posture(period: CompositePeriod) -> None:
    if period.status == "BLOCKED" and (
        period.return_value is not None or period.cumulative_return is not None
    ):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_BLOCKED_VALUE")
    if period.status == "READY" and (
        period.return_value is None or period.cumulative_return is None or period.member_count == 0
    ):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_READY_EVIDENCE_MISSING")
    if period.return_value is not None and (
        period.return_view is None or period.reporting_currency is None
    ):
        raise CompositeEvidenceRefused("COMPOSITE_REPORT_FINANCIAL_CONTEXT_MISSING")
