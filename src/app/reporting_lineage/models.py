from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SnapshotPosture = Literal[
    "complete",
    "partial",
    "unavailable",
    "not_supported",
    "redacted",
    "error",
]

UpstreamFailureCategory = Literal[
    "none",
    "partial_data",
    "unsupported_input",
    "upstream_unavailable",
    "upstream_error",
    "timeout",
    "redacted",
]


class ReportInputSnapshotCreateRequest(BaseModel):
    report_job_id: str = Field(
        ...,
        description="Opaque report job identifier that owns this snapshot.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    report_type: str = Field(
        ...,
        description="Report type captured by this input snapshot.",
        examples=["portfolio_review"],
    )
    report_data_contract_version: str = Field(
        ...,
        description=(
            "Version of the machine-readable report data contract captured in the snapshot."
        ),
        examples=["v1"],
    )
    portfolio_scope: dict[str, Any] = Field(
        ...,
        description="Portfolio scope captured for the snapshot.",
        examples=[{"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]}],
    )
    as_of_date: date = Field(
        ...,
        description="Business as-of date represented by the snapshot.",
        examples=["2026-04-22"],
    )
    snapshot_payload: dict[str, Any] = Field(
        ...,
        description=(
            "Support-safe snapshot payload stored inline for deterministic hashing and lookup."
        ),
        examples=[{"report_id": "portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22"}],
    )
    snapshot_storage_ref: str | None = Field(
        default=None,
        description=(
            "Optional governed external storage reference when the complete raw payload is stored "
            "outside the database."
        ),
        examples=["s3://lotus-report/snapshots/rjob_83ca965c50334c40a17d2b8cc94873a5.json"],
    )
    report_revision_id: str | None = Field(
        default=None,
        description=(
            "Canonical report-revision identity minted for a successful capture; absent for "
            "failed captures, which record no report facts."
        ),
        examples=["rrv3_7a5486f4a7ef1962f27fe67c6ef392fd0da0dfc7c98a84e426238637f4a5b7dd"],
    )
    series_digest: str | None = Field(
        default=None,
        description="Digest of the canonical report series key behind the revision.",
    )
    source_revision_digest: str | None = Field(
        default=None,
        description="Digest of the source revision vector behind the revision.",
    )
    factual_content_digest: str | None = Field(
        default=None,
        description=(
            "Digest of the payload's factual content under the versioned capture-instance "
            "boundary; distinct from snapshot_hash, which covers the complete stored bytes."
        ),
    )
    factual_boundary_version: str | None = Field(
        default=None,
        description="Version of the factual-content boundary the digest was computed under.",
        examples=["fb1"],
    )
    source_revision_vector: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The canonical source revision vector persisted verbatim: per-source stated "
            "evidence plus the evidence-computed coverage claim."
        ),
    )
    source_cut_coherence: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The evaluated source-cut coherence verdict (status, policy_version, detail): "
            "whether the STATED source cuts share the report's business date. Policy-derived, "
            "never part of the revision preimage; absent on failed captures and pre-policy "
            "history."
        ),
    )
    supportability_status: SnapshotPosture = Field(
        ...,
        description="Supportability posture for the captured snapshot.",
        examples=["complete"],
    )
    completeness_status: SnapshotPosture = Field(
        ...,
        description="Completeness posture for the captured snapshot.",
        examples=["complete"],
    )
    lineage_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Compact lineage summary captured with the snapshot.",
        examples=[{"source_services": ["lotus-core", "lotus-performance", "lotus-risk"]}],
    )
    captured_at: datetime = Field(
        ...,
        description="UTC timestamp when snapshot capture completed.",
        examples=["2026-04-22T09:00:03Z"],
    )
    correlation_id: str = Field(
        ...,
        description="End-to-end correlation identifier linked to the captured snapshot.",
        examples=["corr-portfolio-review-1"],
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace identifier linked to the captured snapshot.",
        examples=["4bf92f3577b34da6a3ce929d0e0e4736"],
    )


class ReportInputSnapshotRecord(BaseModel):
    snapshot_id: str = Field(
        ...,
        description="Opaque durable snapshot identifier.",
        examples=["rsnap_8c0c8f6fc2d947b89cb451d9f4f5d9bf"],
    )
    report_job_id: str = Field(
        ...,
        description="Opaque report job identifier that owns this snapshot.",
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    report_type: str = Field(
        ...,
        description="Report type captured by this snapshot.",
        examples=["portfolio_review"],
    )
    report_data_contract_version: str = Field(
        ...,
        description=(
            "Version of the machine-readable report data contract captured in the snapshot."
        ),
        examples=["v1"],
    )
    portfolio_scope: dict[str, Any] = Field(
        ...,
        description="Portfolio scope captured for the snapshot.",
        examples=[{"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]}],
    )
    as_of_date: date = Field(
        ...,
        description="Business as-of date represented by the snapshot.",
        examples=["2026-04-22"],
    )
    snapshot_payload: dict[str, Any] = Field(
        ...,
        description=(
            "Support-safe inline snapshot payload used for deterministic reconstruction and APIs."
        ),
        examples=[{"report_id": "portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22"}],
    )
    snapshot_hash: str = Field(
        ...,
        description="Canonical SHA-256 hash of the inline snapshot payload.",
        examples=["sha256:7a5486f4a7ef1962f27fe67c6ef392fd0da0dfc7c98a84e426238637f4a5b7dd"],
    )
    snapshot_storage_ref: str | None = Field(
        default=None,
        description=(
            "Optional governed external storage reference for large or sensitive raw payloads."
        ),
        examples=["s3://lotus-report/snapshots/rjob_83ca965c50334c40a17d2b8cc94873a5.json"],
    )
    report_revision_id: str | None = Field(
        default=None,
        description=(
            "Canonical report-revision identity minted at capture. NULL on failed captures "
            "and on rows captured before revision identity existed - history is never "
            "relabelled with identities it did not state."
        ),
        examples=["rrv3_7a5486f4a7ef1962f27fe67c6ef392fd0da0dfc7c98a84e426238637f4a5b7dd"],
    )
    series_digest: str | None = Field(
        default=None,
        description="Digest of the canonical report series key behind the revision.",
    )
    source_revision_digest: str | None = Field(
        default=None,
        description="Digest of the source revision vector behind the revision.",
    )
    factual_content_digest: str | None = Field(
        default=None,
        description=(
            "Digest of the payload's factual content under the versioned capture-instance "
            "boundary; distinct from snapshot_hash, which covers the complete stored bytes."
        ),
    )
    factual_boundary_version: str | None = Field(
        default=None,
        description="Version of the factual-content boundary the digest was computed under.",
        examples=["fb1"],
    )
    source_revision_vector: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The canonical source revision vector persisted verbatim: per-source stated "
            "evidence plus the evidence-computed coverage claim."
        ),
    )
    source_cut_coherence: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The evaluated source-cut coherence verdict (status, policy_version, detail): "
            "whether the STATED source cuts share the report's business date. Policy-derived, "
            "never part of the revision preimage; absent on failed captures and pre-policy "
            "history."
        ),
    )
    supportability_status: SnapshotPosture = Field(
        ...,
        description="Supportability posture for the captured snapshot.",
        examples=["complete"],
    )
    completeness_status: SnapshotPosture = Field(
        ...,
        description="Completeness posture for the captured snapshot.",
        examples=["complete"],
    )
    lineage_summary: dict[str, Any] = Field(
        ...,
        description="Compact lineage summary captured with the snapshot.",
        examples=[{"source_services": ["lotus-core", "lotus-performance", "lotus-risk"]}],
    )
    captured_at: datetime = Field(
        ...,
        description="UTC timestamp when snapshot capture completed.",
        examples=["2026-04-22T09:00:03Z"],
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the durable snapshot row was written.",
        examples=["2026-04-22T09:00:03Z"],
    )
    correlation_id: str = Field(
        ...,
        description="End-to-end correlation identifier linked to the captured snapshot.",
        examples=["corr-portfolio-review-1"],
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace identifier linked to the captured snapshot.",
        examples=["4bf92f3577b34da6a3ce929d0e0e4736"],
    )


class ReportUpstreamCallCreateRequest(BaseModel):
    service_name: str = Field(
        ...,
        description="Authoritative upstream Lotus service called during snapshot capture.",
        examples=["lotus-core"],
    )
    endpoint: str = Field(
        ...,
        description="Concrete upstream API path used during the call.",
        examples=["/reporting/portfolio-summary/query"],
    )
    method: str = Field(
        ...,
        description="HTTP method used for the upstream call.",
        examples=["POST"],
    )
    contract_version: str = Field(
        ...,
        description="Observed or governed upstream contract version for this call.",
        examples=["v1"],
    )
    request_hash: str = Field(
        ...,
        description="Canonical SHA-256 hash of the support-safe request payload.",
        examples=["sha256:0f5de8ef5cf305bf2e38ed33139e1df8f06fdf531f80903c123c25f6d8c09780"],
    )
    response_hash: str | None = Field(
        default=None,
        description="Canonical SHA-256 hash of the support-safe response payload when available.",
        examples=["sha256:9de9c193650baf615ff8dca094d10ff18bdaabf0915963c4b3d74a3a07844f52"],
    )
    response_ref: str | None = Field(
        default=None,
        description=(
            "Optional governed reference used when response content is redacted or externalized."
        ),
        examples=["redacted:inline-hash-only"],
    )
    status_code: int = Field(
        ...,
        description="HTTP status code or equivalent outcome recorded for the upstream call.",
        examples=[200],
    )
    latency_ms: int = Field(
        ...,
        description="Measured upstream round-trip latency in milliseconds.",
        examples=[184],
    )
    supportability_status: SnapshotPosture = Field(
        ...,
        description="Supportability posture for this upstream input.",
        examples=["complete"],
    )
    completeness_status: SnapshotPosture = Field(
        ...,
        description="Completeness posture for this upstream input.",
        examples=["complete"],
    )
    failure_category: UpstreamFailureCategory = Field(
        ...,
        description="Machine-readable failure or exception category for the upstream call.",
        examples=["none"],
    )
    failure_message: str | None = Field(
        default=None,
        description="Support-safe failure detail for the upstream call.",
        examples=["Upstream request timed out before a complete response was returned."],
    )
    captured_at: datetime = Field(
        ...,
        description="UTC timestamp when the upstream call completed or failed.",
        examples=["2026-04-22T09:00:02Z"],
    )
    correlation_id: str = Field(
        ...,
        description="End-to-end correlation identifier associated with the upstream call.",
        examples=["corr-portfolio-review-1"],
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace identifier associated with the upstream call.",
        examples=["4bf92f3577b34da6a3ce929d0e0e4736"],
    )


class ReportUpstreamCallRecord(BaseModel):
    upstream_call_id: str = Field(
        ...,
        description="Opaque durable identifier for one recorded upstream call.",
        examples=["ruc_7c5d4f1e4cb6455fa11c06821c57b88f"],
    )
    snapshot_id: str = Field(
        ...,
        description="Durable snapshot identifier that owns this upstream-call evidence row.",
        examples=["rsnap_8c0c8f6fc2d947b89cb451d9f4f5d9bf"],
    )
    service_name: str = Field(
        ...,
        description="Authoritative upstream Lotus service called during snapshot capture.",
        examples=["lotus-core"],
    )
    endpoint: str = Field(
        ...,
        description="Concrete upstream API path used during the call.",
        examples=["/reporting/portfolio-summary/query"],
    )
    method: str = Field(
        ...,
        description="HTTP method used for the upstream call.",
        examples=["POST"],
    )
    contract_version: str = Field(
        ...,
        description="Observed or governed upstream contract version for this call.",
        examples=["v1"],
    )
    request_hash: str = Field(
        ...,
        description="Canonical SHA-256 hash of the support-safe request payload.",
        examples=["sha256:0f5de8ef5cf305bf2e38ed33139e1df8f06fdf531f80903c123c25f6d8c09780"],
    )
    response_hash: str | None = Field(
        default=None,
        description="Canonical SHA-256 hash of the support-safe response payload when available.",
        examples=["sha256:9de9c193650baf615ff8dca094d10ff18bdaabf0915963c4b3d74a3a07844f52"],
    )
    response_ref: str | None = Field(
        default=None,
        description=(
            "Optional governed reference used when response content is redacted or externalized."
        ),
        examples=["redacted:inline-hash-only"],
    )
    status_code: int = Field(
        ...,
        description="HTTP status code or equivalent outcome recorded for the upstream call.",
        examples=[200],
    )
    latency_ms: int = Field(
        ...,
        description="Measured upstream round-trip latency in milliseconds.",
        examples=[184],
    )
    supportability_status: SnapshotPosture = Field(
        ...,
        description="Supportability posture for this upstream input.",
        examples=["complete"],
    )
    completeness_status: SnapshotPosture = Field(
        ...,
        description="Completeness posture for this upstream input.",
        examples=["complete"],
    )
    failure_category: UpstreamFailureCategory = Field(
        ...,
        description="Machine-readable failure or exception category for the upstream call.",
        examples=["none"],
    )
    failure_message: str | None = Field(
        default=None,
        description="Support-safe failure detail for the upstream call.",
        examples=[None],
    )
    captured_at: datetime = Field(
        ...,
        description="UTC timestamp when the upstream call completed or failed.",
        examples=["2026-04-22T09:00:02Z"],
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the durable upstream-call row was written.",
        examples=["2026-04-22T09:00:02Z"],
    )
    correlation_id: str = Field(
        ...,
        description="End-to-end correlation identifier associated with the upstream call.",
        examples=["corr-portfolio-review-1"],
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace identifier associated with the upstream call.",
        examples=["4bf92f3577b34da6a3ce929d0e0e4736"],
    )


class ReportSnapshotLineageResponse(BaseModel):
    snapshot: ReportInputSnapshotRecord = Field(
        ...,
        description="Durable report input snapshot associated with the returned lineage rows.",
    )
    upstream_calls: list[ReportUpstreamCallRecord] = Field(
        ...,
        description="Append-only upstream-call lineage rows captured for this snapshot.",
    )
