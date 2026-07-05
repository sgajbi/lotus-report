from __future__ import annotations

from typing import Any


class ReportingApplicationError(Exception):
    """Base application error for report orchestration failures."""

    def __init__(self, detail: Any):
        self.detail = detail
        super().__init__(str(detail))


class ReportingValidationError(ReportingApplicationError):
    """Caller request or unsupported source-data shape cannot be processed."""


class ReportingNotFoundError(ReportingApplicationError):
    """Requested reporting source or portfolio was not found."""


class ReportingUpstreamError(ReportingApplicationError):
    """Required upstream report source failed or returned an invalid contract."""
