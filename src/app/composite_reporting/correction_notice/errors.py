"""Support-safe notice refusal categories, never supplier error messages."""

from app.composite_reporting.admission import CompositeEvidenceRefused


class NoticeEvidenceRefused(CompositeEvidenceRefused):
    """A bounded terminal notice refusal, without source free text."""
