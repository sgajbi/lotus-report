from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AttentionResourceType = Literal["report_job", "batch_item"]
AttentionSeverity = Literal["warning", "critical"]
AttentionType = Literal["stuck_state", "sla_breach"]


class ReportingAttentionEvent(BaseModel):
    resource_type: AttentionResourceType = Field(
        ...,
        description="Type of durable reporting resource requiring operator attention.",
        examples=["report_job"],
    )
    resource_id: str = Field(
        ...,
        description=(
            "Opaque durable resource identifier. Payloads and portfolio scope are excluded."
        ),
        examples=["rjob_83ca965c50334c40a17d2b8cc94873a5"],
    )
    parent_resource_id: str | None = Field(
        default=None,
        description="Opaque parent resource identifier for batch-item attention events.",
        examples=["rbch_2f6d1a8f2ef24f019e7d7f37507f352c"],
    )
    attention_type: AttentionType = Field(
        ...,
        description="Bounded attention category emitted by the operations scanner.",
        examples=["stuck_state"],
    )
    severity: AttentionSeverity = Field(
        ...,
        description="Bounded severity derived from elapsed age and SLA threshold.",
        examples=["warning"],
    )
    status: str = Field(
        ...,
        description="Current source-backed lifecycle status.",
        examples=["rendering"],
    )
    reason: str = Field(
        ...,
        description="Bounded machine-readable reason for operator triage.",
        examples=["report_job_active_state_exceeded_stuck_threshold"],
    )
    age_seconds: int = Field(
        ...,
        ge=0,
        description="Seconds elapsed since the resource's latest operational timestamp.",
        examples=[1800],
    )
    threshold_seconds: int = Field(
        ...,
        ge=0,
        description="Threshold that caused this event to be emitted.",
        examples=[900],
    )
    recommended_action: str = Field(
        ...,
        description="Support-safe next action for operators.",
        examples=[
            "Inspect report job diagnostics and replay only if the source failure is retryable."
        ],
    )
    evidence_url: str = Field(
        ...,
        description="Relative source-backed endpoint for deeper support-safe evidence.",
        examples=["/reports/jobs/rjob_83ca965c50334c40a17d2b8cc94873a5/diagnostics"],
    )
    observed_at: datetime = Field(
        ...,
        description="UTC timestamp when the scanner observed this attention event.",
    )


class ReportingAttentionScanResponse(BaseModel):
    scan_id: str = Field(
        ...,
        description="Opaque identifier for this deterministic attention scan.",
        examples=["rasc_20260428T020304Z"],
    )
    scanned_at: datetime = Field(
        ...,
        description="UTC timestamp for the attention scan.",
    )
    report_job_stuck_threshold_seconds: int = Field(
        ...,
        ge=1,
        description="Configured age threshold for active report-job stuck-state events.",
        examples=[900],
    )
    batch_item_stuck_threshold_seconds: int = Field(
        ...,
        ge=1,
        description="Configured age threshold for active batch-item stuck-state events.",
        examples=[900],
    )
    sla_breach_threshold_seconds: int = Field(
        ...,
        ge=1,
        description="Configured age threshold for critical SLA-breach events.",
        examples=[3600],
    )
    event_count: int = Field(
        ...,
        ge=0,
        description="Number of attention events emitted in this bounded scan.",
        examples=[1],
    )
    events: list[ReportingAttentionEvent] = Field(
        ...,
        description="Bounded support-safe attention events. Raw payloads are never returned.",
    )
