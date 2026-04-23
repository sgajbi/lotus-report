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
