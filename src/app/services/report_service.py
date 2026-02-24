from datetime import UTC, datetime
from uuid import uuid4

from app.models.contracts import ReportRequest, ReportResponse


class ReportService:
    def generate_report(self, request: ReportRequest) -> ReportResponse:
        report_id = f"rep_{uuid4().hex[:12]}"
        download_url = None
        if request.output_format == "PDF":
            download_url = f"/reports/{report_id}/download"
        return ReportResponse(
            reportId=report_id,
            reportType=request.report_type,
            outputFormat=request.output_format,
            generatedAt=datetime.now(UTC),
            downloadUrl=download_url,
        )
