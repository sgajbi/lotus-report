from typing import Annotated

from fastapi import APIRouter, Depends

from app.observability import correlation_id_var, trace_id_var
from app.report_ordering_catalogue.models import (
    REPORT_ORDERING_CATALOGUE_EXAMPLE,
    ReportOrderingCatalogueResponse,
)
from app.report_ordering_catalogue.service import (
    ReportOrderingCatalogueService,
    build_report_ordering_catalogue_service,
)

router = APIRouter(prefix="/integration", tags=["Integration"])


def get_report_ordering_catalogue_service() -> ReportOrderingCatalogueService:
    return build_report_ordering_catalogue_service()


@router.get(
    "/report-ordering-catalogue",
    response_model=ReportOrderingCatalogueResponse,
    summary="Get report ordering catalogue",
    description=(
        "Returns the versioned, Report-owned business catalogue used by product consumers to "
        "present supported report families, ordering modes, formats, configuration fields, and "
        "sections. Availability reflects live rendering supportability. Caller entitlement and "
        "portfolio eligibility remain Gateway responsibilities."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": REPORT_ORDERING_CATALOGUE_EXAMPLE,
                        "examples": {
                            "available_catalogue": {
                                "summary": "Source-backed report choices",
                                "value": REPORT_ORDERING_CATALOGUE_EXAMPLE,
                            }
                        },
                    }
                }
            }
        }
    },
)
async def get_report_ordering_catalogue(
    service: Annotated[
        ReportOrderingCatalogueService,
        Depends(get_report_ordering_catalogue_service),
    ],
) -> ReportOrderingCatalogueResponse:
    return await service.get_catalogue(
        correlation_id=correlation_id_var.get() or None,
        trace_id=trace_id_var.get() or None,
    )
