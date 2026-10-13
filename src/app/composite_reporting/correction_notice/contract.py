"""Closed correction-notice data and custody contracts with inherited source semantics."""

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, WithJsonSchema, field_validator, model_validator

from app.composite_reporting.correction_notice.selection import (
    SOURCE_TABLES,
    NoticeDigest,
    NoticeId,
    NoticeModel,
    SelectionTuple,
)
from app.composite_reporting.correction_notice.source_identity import (
    CompositeReportIdentityV8,
    embedded_schema,
    inherited_schema,
    validate_inherited,
)
from app.composite_reporting.models import CompositeReportSelection
from app.composite_reporting.source_context_contract import CompositeSourceContextReportData
from app.composite_reporting.table_contract import CompositeColumn, require_complete_table
from app.reporting_identity.identity import SourceRevisionVector
from app.reporting_jobs.models import ReportStatusEvent

Side = Literal["before", "after"]
Availability = Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN", "NOT_APPLICABLE"]
BareDigest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
V8Payload = Annotated[
    dict[str, Any],
    WithJsonSchema(embedded_schema(CompositeSourceContextReportData.model_json_schema())),
]
ArchiveResponse = Annotated[
    dict[str, Any],
    WithJsonSchema(embedded_schema(inherited_schema("archive-document.v8.schema.json"))),
]


class CaptureIdentity(NoticeModel):
    report_revision_id: str = Field(strict=True, pattern=r"^rrv3_[0-9a-f]{64}$")
    series_digest: BareDigest
    source_revision_digest: BareDigest
    factual_content_digest: NoticeDigest


class SourceScope(NoticeModel):
    tenant_id: NoticeId
    composite_id: NoticeId
    period_start: str
    period_end: str
    reporting_currency: str = Field(strict=True, pattern=r"^[A-Z]{3}$")
    return_view: NoticeId
    methodology: NoticeId
    engine_version: NoticeId


class Generation(NoticeModel):
    selection: SelectionTuple
    contract_version: Literal["composite_review.v8"]
    snapshot_response_bytes_sha256: NoticeDigest
    payload: V8Payload
    source_revision_vector: Annotated[
        dict[str, Any], WithJsonSchema(embedded_schema(SourceRevisionVector.model_json_schema()))
    ]
    source_identity: SourceScope
    capture_identity: CaptureIdentity

    @model_validator(mode="after")
    def require_source(self) -> "Generation":
        from app.composite_reporting.correction_notice.selection import SCOPE_AXES

        CompositeSourceContextReportData.model_validate(self.payload)
        SourceRevisionVector.model_validate(self.source_revision_vector)
        selection = CompositeReportSelection.model_validate(self.payload["selection"])
        scope = selection.model_dump(mode="json")
        if self.source_identity.model_dump() != {key: scope[key] for key in SCOPE_AXES}:
            raise ValueError("COMPOSITE_NOTICE_SCOPE_CONFLICT")
        if [t["table_id"] for t in self.payload["tables"]] != list(SOURCE_TABLES):
            raise ValueError("COMPOSITE_NOTICE_SOURCE_LAYOUT_UNSUPPORTED")
        if self.capture_identity.report_revision_id != self.selection.report_revision_id:
            raise ValueError("COMPOSITE_NOTICE_SOURCE_REVISION_CONFLICT")
        return self


class Generations(NoticeModel):
    before: Generation
    after: Generation


class Coordinate(NoticeModel):
    generation: Side
    table_index: int = Field(strict=True, ge=0, le=13)
    row_index: int = Field(strict=True, ge=0, le=9999)
    column_index: int = Field(strict=True, ge=0, le=31)
    table_id: NoticeId
    row_id: NoticeId
    column_id: NoticeId
    source_pointer: str = Field(strict=True, min_length=1, max_length=4096)


class CoordinateKey(NoticeModel):
    table_id: NoticeId
    row_id: NoticeId
    column_id: NoticeId


class PairIndexItem(NoticeModel):
    row_id: NoticeId
    coordinate: CoordinateKey
    presence: Literal["BOTH_PRESENT", "BEFORE_ONLY", "AFTER_ONLY"]
    before_ref: Coordinate | None
    after_ref: Coordinate | None


class Node(NoticeModel):
    position: int = Field(strict=True, ge=0, le=3)
    role: Literal["O", "O_rr", "C", "C_rr"]
    document_id: NoticeId
    artifact_sha256: NoticeDigest
    tenant_id: NoticeId
    report_job_id: NoticeId
    snapshot_id: NoticeId
    report_revision_id: NoticeId
    snapshot_hash: NoticeDigest
    economic_generation: Side
    document_response: ArchiveResponse
    document_response_digest: NoticeDigest

    @field_validator("document_response")
    @classmethod
    def require_document(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_inherited("archive-document.v8.schema.json", value)
        CompositeReportIdentityV8.model_validate(value["composite_report_identity"])
        return value


class ArchiveRelationshipRecord(NoticeModel):
    """Immutable eight-field evidence; never an original POST response.

    Native Archive197 schema/KAT are pinned from accepted commit 036c1412.
    Validation does not serialize or rewrite the captured raw timestamp.
    """

    lifecycle_relationship_id: NoticeId
    source_document_id: NoticeId
    target_document_id: NoticeId
    transition_type: Literal["supersede", "correct", "reissue"]
    transition_reason: str = Field(strict=True, min_length=1)
    transition_reason_code: str = Field(strict=True, min_length=1)
    requested_by: str = Field(strict=True, min_length=1)
    requested_at: AwareDatetime


class Acknowledgement(NoticeModel):
    kind: Literal["ARCHIVE_RELATIONSHIP_RECORD", "REPORT_LINEAGE_EVENT"]
    source_capture: str = Field(strict=True, min_length=1, max_length=512)
    raw_payload: Annotated[
        dict[str, Any],
        WithJsonSchema(
            {
                "anyOf": [
                    embedded_schema(inherited_schema("archive-relationship.v1.schema.json")),
                    embedded_schema(
                        {**ReportStatusEvent.model_json_schema(), "additionalProperties": False}
                    ),
                ]
            }
        ),
    ]
    digest: NoticeDigest

    @model_validator(mode="after")
    def require_immutable_relationship_record(self) -> "Acknowledgement":
        if self.kind == "ARCHIVE_RELATIONSHIP_RECORD":
            validate_inherited("archive-relationship.v1.schema.json", self.raw_payload)
            ArchiveRelationshipRecord.model_validate(self.raw_payload)
        return self


class Edge(NoticeModel):
    position: int = Field(strict=True, ge=0, le=2)
    relationship_id: NoticeId
    source_document_id: NoticeId
    target_document_id: NoticeId
    transition_type: Literal["correct"]
    semantic_role: Literal["TECHNICAL_RERENDER", "ECONOMIC_CORRECTION"]
    source_generation: Side
    target_generation: Side
    acknowledgement: Acknowledgement


class ArchivePath(NoticeModel):
    nodes: list[Node] = Field(min_length=4, max_length=4)
    edges: list[Edge] = Field(min_length=3, max_length=3)


class Obligation(NoticeModel):
    status: Literal["CAPTURED_INTERNAL", "UNAVAILABLE", "UNKNOWN", "NOT_APPLICABLE"]
    reason_code: str = Field(strict=True, pattern=r"^[A-Z][A-Z0-9_]{0,127}$")
    evidence_refs: list[str] = Field(max_length=32)


class Obligations(NoticeModel):
    before_after_results: Obligation
    affected_periods: Obligation
    reason: Obligation
    materiality: Obligation
    report_approval: Obligation
    report_replacements: Obligation
    explanation_delta_interaction: Obligation
    recipient_distribution: Obligation


class NoticeCell(NoticeModel):
    canonical_value: str | None
    availability: Availability
    reason_codes: list[Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{1,128}$")]] = Field(
        max_length=32
    )
    source_pointer: str = Field(
        pattern=r"^/(generations/(before|after)/payload/(source_response|selection|report_facts|source_products|definition|supplied_memberships)/|notice_facts/)"
    )

    @model_validator(mode="after")
    def require_availability(self) -> "NoticeCell":
        if self.availability == "AVAILABLE":
            if self.canonical_value is None:
                raise ValueError("COMPOSITE_NOTICE_AVAILABLE_NULL")
        elif self.canonical_value is not None or not self.reason_codes:
            raise ValueError("COMPOSITE_NOTICE_UNAVAILABLE_VALUE")
        if self.source_pointer.startswith("/notice_facts/") and any(
            not code[0].isupper() or code != code.upper() for code in self.reason_codes
        ):
            raise ValueError("COMPOSITE_NOTICE_GENERATED_REASON_INVALID")
        return self


class NoticeRow(NoticeModel):
    row_id: NoticeId
    cells: dict[str, NoticeCell]


class NoticeTable(NoticeModel):
    table_id: NoticeId
    title: NoticeId
    columns: list[CompositeColumn] = Field(min_length=1, max_length=32)
    rows: list[NoticeRow] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def require_rows(self) -> "NoticeTable":
        require_complete_table(
            [column.column_id for column in self.columns],
            [(row.row_id, set(row.cells)) for row in self.rows],
        )
        return self


class ScopeFact(NoticeModel):
    field: str
    value: str
    source_pointer: str


class PairFact(NoticeModel):
    source_table_id: str
    source_row_id: str
    source_column_id: str
    presence: Literal["BOTH_PRESENT", "BEFORE_ONLY", "AFTER_ONLY"]
    before_cell_ref: str | None
    after_cell_ref: str | None


class PathFact(NoticeModel):
    edge_index: str
    source_document_id: str
    target_document_id: str
    relationship_id: str
    wire_transition_type: str
    semantic_role: str
    source_generation: str
    target_generation: str
    acknowledgement_evidence_digest: str


class ObligationFact(NoticeModel):
    obligation_id: str
    status: str
    reason_code: str
    evidence_refs: str


class NoticeFacts(NoticeModel):
    scope: list[ScopeFact] = Field(min_length=23, max_length=23)
    pair_index: list[PairFact] = Field(min_length=1, max_length=10000)
    archive_path: list[PathFact] = Field(min_length=3, max_length=3)
    obligations: list[ObligationFact] = Field(min_length=8, max_length=8)


class CorrectionNoticeData(NoticeModel):
    contract_version: Literal["composite_review.v9"]
    comparison_profile: Literal["RETAINED_SAME_SCOPE_CORRECTION_NOTICE_V1"]
    tenant_id: NoticeId
    qualification: Literal["EXPLICIT_RETAINED_CALCULATED_REPLAY"]
    publication_state: Literal["NOT_ATTESTED"]
    notice_completeness: Literal["PARTIAL"]
    generations: Generations
    archive_path: ArchivePath
    obligations: Obligations
    pair_index: list[PairIndexItem] = Field(min_length=1, max_length=10000)
    pair_fingerprint: NoticeDigest
    manifest_digest: NoticeDigest
    notice_facts: NoticeFacts
    tables: list[NoticeTable] = Field(min_length=32, max_length=32)

    @model_validator(mode="after")
    def require_complete_notice(self) -> "CorrectionNoticeData":
        from app.composite_reporting.correction_notice.admission import validate_notice

        validate_notice(self.model_dump(mode="json"))
        return self


class NoticeSourceGenerationCustody(SelectionTuple):
    source_document_id: NoticeId
    source_identity: CompositeReportIdentityV8


class CustodyGenerations(NoticeModel):
    before: NoticeSourceGenerationCustody
    after: NoticeSourceGenerationCustody


class OwnNoticeCustodyBinding(NoticeModel):
    contract_version: Literal["composite_review.v9"]
    comparison_profile: Literal["RETAINED_SAME_SCOPE_CORRECTION_NOTICE_V1"]
    qualification: Literal["EXPLICIT_RETAINED_CALCULATED_REPLAY"]
    publication_state: Literal["NOT_ATTESTED"]
    notice_completeness: Literal["PARTIAL"]
    pair_fingerprint: NoticeDigest
    manifest_digest: NoticeDigest
    generations: CustodyGenerations
    series_digest: BareDigest
    source_revision_digest: BareDigest
    factual_content_digest: BareDigest
