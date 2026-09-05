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
]
