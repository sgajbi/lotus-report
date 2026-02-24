from fastapi import APIRouter, Query

from app.config import settings
from app.models.contracts import IntegrationCapabilitiesResponse

router = APIRouter(prefix="/integration", tags=["Integration"])


@router.get(
    "/capabilities",
    response_model=IntegrationCapabilitiesResponse,
    summary="Get Integration Capabilities",
    description=(
        "Returns RAS integration capabilities for BFF/DPM contract negotiation and "
        "feature toggling."
    ),
)
def get_capabilities(
    consumer_system: str = Query("BFF", alias="consumerSystem"),
    tenant_id: str = Query("default", alias="tenantId"),
) -> IntegrationCapabilitiesResponse:
    _ = (consumer_system, tenant_id)
    return IntegrationCapabilitiesResponse(
        contractVersion=settings.contract_version,
        features=[
            {"key": "ras.reporting.portfolio_summary", "enabled": True},
            {"key": "ras.reporting.portfolio_review", "enabled": True},
            {"key": "ras.aggregation.portfolio_snapshot", "enabled": True},
        ],
        workflows=[
            {"workflow_key": "portfolio_reporting", "enabled": True},
            {"workflow_key": "portfolio_review_reporting", "enabled": True},
        ],
        supportedInputModes=["pas_ref"],
    )
