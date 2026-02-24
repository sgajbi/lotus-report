from fastapi import APIRouter

from app.models.contracts import ReportRequest, ReportResponse
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.post(
    "",
    response_model=ReportResponse,
    summary="Generate report",
    description=(
        "Generates a report metadata record from aggregated PAS+PA backed views. "
        "Current slice supports JSON metadata and PDF placeholder download URL."
    ),
)
def generate_report(request: ReportRequest) -> ReportResponse:
    return ReportService().generate_report(request)
