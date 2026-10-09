"""Versioned Report-owned semantic handoff to Render, with executable admission."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import model_validator

from app.composite_reporting.admission import CompositeEvidenceRefused, admit_composite_response
from app.composite_reporting.models import (
    CompositeCalculatedResponse,
    CompositeReportSelection,
    Digest,
)
from app.composite_reporting.table_contract import CompositeTable, TableModel, validate_cell_lineage


class CompositeReportData(TableModel):
    contract_version: Literal["composite_review.v1"]
    qualification: Literal["EXPLICIT_RETAINED_CALCULATED_REPLAY"]
    publication_state: Literal["NOT_ATTESTED"]
    tenant_id: str
    selection: CompositeReportSelection
    source_response_digest: Digest
    source_response: dict[str, Any]
    report_facts: dict[str, Any]
    tables: list[CompositeTable]

    @model_validator(mode="after")
    def require_pinned_dataset_and_cells(self) -> CompositeReportData:
        admitted = admit_composite_response(
            selection=self.selection,
            admitted_tenant_id=self.tenant_id,
            status_code=200,
            payload=self.source_response,
        )
        if self.source_response_digest != admitted["source_response_digest"]:
            raise CompositeEvidenceRefused("COMPOSITE_REPORT_DATASET_DIGEST_CONFLICT")
        if self.report_facts.get("authority") != {
            "receipt": None,
            "control_revision": None,
            "availability": "UNAVAILABLE",
            "reason_code": "SOURCE_AUTHORITY_NOT_ATTESTED",
        }:
            raise CompositeEvidenceRefused("COMPOSITE_REPORT_AUTHORITY_NOT_SUPPORTED")
        if (
            self.report_facts.get("qualification") != self.qualification
            or self.report_facts.get("publication_state") != self.publication_state
        ):
            raise CompositeEvidenceRefused("COMPOSITE_REPORT_AUTHORITY_CONFLICT")
        validate_cell_lineage(self.model_dump(mode="json"), self.tables)
        return self


def composite_report_schema() -> dict[str, Any]:
    schema = CompositeReportData.model_json_schema()
    # Runtime validation retains the raw dictionary byte spelling. The shared
    # schema exposes the same consumed producer shape without round-tripping
    # canonical financial text through a typed Decimal serialization.
    producer = CompositeCalculatedResponse.model_json_schema()
    definitions = schema.setdefault("$defs", {})
    definitions.update(producer.pop("$defs", {}))
    definitions["CompositeCalculatedResponse"] = producer
    schema["properties"]["source_response"] = {"$ref": "#/$defs/CompositeCalculatedResponse"}
    return schema
