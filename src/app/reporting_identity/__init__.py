"""Canonical report series and revision identity (report#283)."""

from app.reporting_identity.capture_binding import revision_for_capture
from app.reporting_identity.identity import (
    CAPTURE_INSTANCE_KEYS,
    FACTUAL_BOUNDARY_VERSION,
    QUALIFYING_REVISION_EVIDENCE_FIELDS,
    REPORT_REVISION_ID_VERSION,
    ReportRevisionIdentity,
    ReportSeriesKey,
    SourceRevision,
    SourceRevisionVector,
    derive_report_revision,
    factual_content_digest,
)
from app.reporting_identity.snapshot_lifecycle import (
    SNAPSHOT_LIFECYCLE_POLICY_REF,
    snapshot_lifecycle_claim,
)
from app.reporting_identity.source_cut_coherence import (
    SOURCE_CUT_COHERENCE_POLICY_VERSION,
    SourceCutCoherence,
    evaluate_source_cut_coherence,
)

__all__ = [
    "CAPTURE_INSTANCE_KEYS",
    "FACTUAL_BOUNDARY_VERSION",
    "QUALIFYING_REVISION_EVIDENCE_FIELDS",
    "REPORT_REVISION_ID_VERSION",
    "ReportRevisionIdentity",
    "ReportSeriesKey",
    "SourceRevision",
    "SourceRevisionVector",
    "derive_report_revision",
    "factual_content_digest",
    "revision_for_capture",
    "SNAPSHOT_LIFECYCLE_POLICY_REF",
    "snapshot_lifecycle_claim",
    "SNAPSHOT_LIFECYCLE_POLICY_REF",
    "snapshot_lifecycle_claim",
    "SOURCE_CUT_COHERENCE_POLICY_VERSION",
    "SourceCutCoherence",
    "evaluate_source_cut_coherence",
]
