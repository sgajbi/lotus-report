from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import HTTPException, status

from app.report_ordering_catalogue.validation import (
    ReportOrderingSubmissionError,
    validate_report_ordering_submission,
)


def enforce_report_ordering_submission(
    *,
    report_family_id: str,
    ordering_mode_id: str,
    requested_output_formats: Sequence[str],
    options: Mapping[str, Any],
) -> None:
    try:
        validate_report_ordering_submission(
            report_family_id=report_family_id,
            ordering_mode_id=ordering_mode_id,
            requested_output_formats=requested_output_formats,
            options=options,
        )
    except ReportOrderingSubmissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
