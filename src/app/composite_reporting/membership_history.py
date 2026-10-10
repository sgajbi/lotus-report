"""Project exact supplied Manage intervals; no eligibility or history engine."""

from datetime import date
from typing import Any, Literal
from urllib.parse import quote

from app.composite_reporting.admission import CompositeEvidenceRefused, response_digest
from app.composite_reporting.eligibility_admission import require_hash
from app.composite_reporting.eligibility_contract import EligibilityMembership
from app.composite_reporting.models import CompositeMembershipPin, CompositeReportSelection, Digest
from app.composite_reporting.table_contract import TableModel


class CapturedMembership(TableModel):
    pin: CompositeMembershipPin
    endpoint: str
    method: Literal["GET"]
    source_response_digest: Digest
    source_response: dict[str, Any]


def membership_endpoint(definition_endpoint: str, pin: CompositeMembershipPin) -> str:
    return f"{definition_endpoint}/membership/{quote(pin.membership_revision, safe='')}"


def require_membership_join(
    selection: CompositeReportSelection,
    definition: dict[str, Any],
    definition_endpoint: str,
    memberships: list[CapturedMembership],
    *,
    additional_selections: list[CompositeReportSelection] | None = None,
) -> None:
    if not memberships:
        return
    by_revision = {item.pin.membership_revision: item for item in memberships}
    if len(by_revision) != len(memberships):
        raise CompositeEvidenceRefused("COMPOSITE_MEMBERSHIP_REVISION_DUPLICATED")
    referenced = {
        window.membership_content_hash
        for vector in [selection, *(additional_selections or [])]
        for window in vector.windows
    }
    actual = {item.pin.content_hash for item in memberships}
    if not referenced.issubset(actual):
        raise CompositeEvidenceRefused("COMPOSITE_MEMBERSHIP_WINDOW_MISSING")
    linked = set(referenced)
    for item in memberships:
        typed = _require_membership_capture(selection, definition, definition_endpoint, item)
        predecessor = _require_parent(typed, by_revision)
        if predecessor is not None and typed.content_hash in referenced:
            linked.add(predecessor.pin.content_hash)
        _require_intervals(typed)
    if actual != linked:
        raise CompositeEvidenceRefused("COMPOSITE_MEMBERSHIP_ATTACHMENT_UNREFERENCED")


def _require_membership_capture(
    selection: CompositeReportSelection,
    definition: dict[str, Any],
    definition_path: str,
    item: CapturedMembership,
) -> EligibilityMembership:
    raw = item.source_response
    typed = EligibilityMembership.model_validate(raw)
    require_hash(raw)
    if (
        item.endpoint != membership_endpoint(definition_path, item.pin)
        or response_digest(raw) != item.source_response_digest
        or item.source_response_digest != item.pin.response_digest
        or typed.content_hash != item.pin.content_hash
        or typed.membership_revision != item.pin.membership_revision
        or typed.tenant_id != selection.tenant_id
        or typed.composite_id != selection.composite_id
        or typed.definition_version != definition["definition_version"]
    ):
        raise CompositeEvidenceRefused("COMPOSITE_SUPPLIED_MEMBERSHIP_CONFLICT")
    return typed


def _require_parent(
    typed: EligibilityMembership, by_revision: dict[str, CapturedMembership]
) -> CapturedMembership | None:
    parent = typed.supersedes_membership_revision
    if parent is None:
        if typed.affected_from is not None or typed.affected_to is not None:
            raise CompositeEvidenceRefused("COMPOSITE_MEMBERSHIP_PARENT_CONFLICT")
        return None
    predecessor = by_revision.get(parent)
    if (
        predecessor is None
        or parent == typed.membership_revision
        or typed.affected_from is None
        or typed.affected_to is None
        or date.fromisoformat(typed.affected_to) < date.fromisoformat(typed.affected_from)
        or predecessor.source_response.get("supersedes_membership_revision") is not None
    ):
        raise CompositeEvidenceRefused("COMPOSITE_MEMBERSHIP_PARENT_CONFLICT")
    return predecessor


def _require_intervals(typed: EligibilityMembership) -> None:
    for decision in typed.decisions:
        first = date.fromisoformat(decision.effective_from)
        if decision.effective_to is not None and date.fromisoformat(decision.effective_to) < first:
            raise CompositeEvidenceRefused("COMPOSITE_MEMBERSHIP_INTERVAL_INVALID")


HISTORY_FIELDS = (
    "membership_revision",
    "supersedes_membership_revision",
    "affected_from",
    "affected_to",
    "portfolio_id",
    "effective_from",
    "effective_to",
    "status",
    "reason_code",
    "discretionary",
    "approval_ref",
    "source_snapshot_id",
)


def project_membership_history(dataset: dict[str, Any]) -> dict[str, Any]:
    """Preserve complete original intervals, including outside the financial cut."""
    from app.composite_reporting.source_context_contract import (
        InceptionCell,
        InceptionRow,
        InceptionTable,
    )
    from app.composite_reporting.table_contract import CompositeColumn, resolve_source_pointer

    rows = []
    for index, capture in enumerate(dataset["supplied_memberships"]):
        raw = capture["source_response"]
        for ordinal, _ in enumerate(raw["decisions"]):
            base = f"/supplied_memberships/{index}/source_response"
            pointers = {
                field: f"{base}/{field}"
                if field in HISTORY_FIELDS[:4]
                else f"{base}/decisions/{ordinal}/{field}"
                for field in HISTORY_FIELDS
            }
            cells = {}
            for field, pointer in pointers.items():
                value = resolve_source_pointer(dataset, pointer)
                if field == "discretionary":
                    value = "true" if value else "false"
                reason = (
                    "OPEN_ENDED_INTERVAL"
                    if field == "effective_to"
                    else "SOURCE_FIELD_NOT_APPLICABLE"
                )
                cells[field] = InceptionCell(
                    canonical_value=value,
                    source_pointer=pointer,
                    availability="AVAILABLE" if value is not None else "NOT_APPLICABLE",
                    reason_codes=[] if value is not None else [reason],
                )
            rows.append(InceptionRow(row_id=f"r{index}:d{ordinal}", cells=cells))
    table = InceptionTable(
        table_id="MembershipHistory",
        title="Supplied membership history",
        columns=[
            CompositeColumn(
                column_id=field,
                label=field.replace("_", " ").capitalize(),
                value_type="TEXT",
                unit="TEXT",
                display_unit="TEXT",
                display_conversion="IDENTITY",
                display_decimal_places=None,
                currency=None,
            )
            for field in HISTORY_FIELDS
        ],
        rows=rows,
    )
    dataset["report_facts"]["uncaptured"].pop("MembershipHistory", None)
    dataset["tables"] = [
        table for table in dataset["tables"] if table["table_id"] != "MembershipHistory"
    ]
    dataset["tables"].append(table.model_dump(mode="json"))
    return dataset
