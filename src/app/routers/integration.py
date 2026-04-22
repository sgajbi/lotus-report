from fastapi import APIRouter, Query

from app.config import settings
from app.models.contracts import IntegrationCapabilitiesResponse

router = APIRouter(prefix="/integration", tags=["Integration"])


@router.get(
    "/capabilities",
    response_model=IntegrationCapabilitiesResponse,
    summary="Get Integration Capabilities",
    description=(
        "Returns lotus-report integration capabilities for "
        "lotus-gateway/lotus-manage contract negotiation and "
        "feature toggling. Callers must use the canonical snake_case query "
        "parameters `consumer_system` and `tenant_id`."
    ),
)
def get_capabilities(
    consumer_system: str = Query(
        "lotus-gateway",
        description=(
            "Consumer system requesting the reporting capability posture. "
            "Send it as the canonical snake_case query parameter `consumer_system`."
        ),
    ),
    tenant_id: str = Query(
        "default",
        description=(
            "Tenant context used for capability publication. "
            "Send it as the canonical snake_case query parameter `tenant_id`."
        ),
    ),
) -> IntegrationCapabilitiesResponse:
    _ = (consumer_system, tenant_id)
    return IntegrationCapabilitiesResponse(
        contractVersion=settings.contract_version,
        features=[
            {"key": "lotus-report.reporting.portfolio_summary", "enabled": True},
            {"key": "lotus-report.reporting.portfolio_review", "enabled": True},
            {"key": "lotus-report.reporting.portfolio_review.first_class.v1", "enabled": True},
            {
                "key": "lotus-report.reporting.portfolio_review.section_readiness.v1",
                "enabled": True,
            },
            {"key": "lotus-report.reporting.portfolio_review.evidence_pack.v1", "enabled": True},
            {
                "key": "lotus-report.reporting.portfolio_review.key_figures.v1",
                "enabled": True,
            },
            {
                "key": "lotus-report.reporting.portfolio_review.advisor_sections.v1",
                "enabled": True,
            },
            {"key": "lotus-report.reporting.portfolio_review.workbench_ready.v1", "enabled": True},
            {"key": "lotus-report.aggregation.portfolio_snapshot", "enabled": True},
        ],
        workflows=[
            {"workflow_key": "portfolio_reporting", "enabled": True},
            {"workflow_key": "portfolio_review_reporting", "enabled": True},
        ],
        supportedInputModes=["portfolio_id"],
    )
