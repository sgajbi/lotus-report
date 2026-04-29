from fastapi import APIRouter, Query

from app.config import settings
from app.models.contracts import IntegrationCapabilitiesResponse
from app.report_batch_orchestrator.contracts import (
    BATCH_CONTROL_API_CAPABILITY_KEY,
    BATCH_MATERIALIZATION_API_CAPABILITY_KEY,
)
from app.reporting_metrics import record_evidence_surface_supportability

router = APIRouter(prefix="/integration", tags=["Integration"])

EVIDENCE_SURFACE_SUPPORTABILITY_KEY = "report.observability.evidence_surface_supportability"
EVIDENCE_SURFACE_FEATURE_KEYS = frozenset(
    {
        "lotus-report.reporting.portfolio_review.evidence_pack.v1",
        "lotus-report.reporting.portfolio_review.job_ledger.v1",
        "lotus-report.reporting.portfolio_review.job_status.v1",
        "lotus-report.reporting.portfolio_review.job_event_history.v1",
        "lotus-report.reporting.portfolio_review.render_submission.v1",
        "lotus-report.reporting.portfolio_review.archive_handoff.v1",
        "lotus-report.reporting.portfolio_review.input_snapshot.v1",
        "lotus-report.reporting.portfolio_review.upstream_lineage.v1",
        "lotus-report.reporting.portfolio_review.snapshot_lookup.v1",
        "lotus-report.reporting.portfolio_review.lineage_lookup.v1",
        "lotus-report.reporting.operations.job_diagnostics.v1",
        "lotus-report.reporting.operations.rerender_from_snapshot.v1",
        "lotus-report.reporting.operations.regenerate_from_upstream.v1",
        "lotus-report.reporting.operations.failed_work_replay.v1",
    }
)


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
    features: list[dict[str, str | bool]] = [
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
            "key": "lotus-report.reporting.portfolio_review.position_pnl.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.performance_contribution.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.source_backed_risk_free.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.source_backed_benchmark.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.transaction_realized_pnl.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.client_profile.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.advisor_briefing.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.ai_readiness.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.upstream_capability_audit.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.advisor_sections.v1",
            "enabled": True,
        },
        {"key": "lotus-report.reporting.portfolio_review.workbench_ready.v1", "enabled": True},
        {
            "key": "lotus-report.reporting.portfolio_review.job_ledger.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.idempotent_job_create.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.job_status.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.job_event_history.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.pre_render_cancel.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.render_submission.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.archive_handoff.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.input_snapshot.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.upstream_lineage.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.snapshot_lookup.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.portfolio_review.lineage_lookup.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.operations.job_diagnostics.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.operations.rerender_from_snapshot.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.operations.regenerate_from_upstream.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.operations.failed_work_replay.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.observability.traceability.v1",
            "enabled": True,
        },
        {
            "key": "lotus-report.reporting.observability.metrics.v1",
            "enabled": True,
        },
        {"key": EVIDENCE_SURFACE_SUPPORTABILITY_KEY, "enabled": True},
        {"key": BATCH_MATERIALIZATION_API_CAPABILITY_KEY, "enabled": True},
        {"key": BATCH_CONTROL_API_CAPABILITY_KEY, "enabled": True},
        {"key": "lotus-report.aggregation.portfolio_snapshot", "enabled": True},
    ]
    workflows: list[dict[str, str | bool]] = [
        {"workflow_key": "portfolio_reporting", "enabled": True},
        {"workflow_key": "portfolio_review_reporting", "enabled": True},
        {"workflow_key": "portfolio_review_report_job", "enabled": True},
    ]
    return IntegrationCapabilitiesResponse(
        contract_version=settings.contract_version,
        features=features,
        workflows=workflows,
        supported_input_modes=["portfolio_id"],
        supportability=_build_evidence_surface_supportability(features, workflows),
    )


def _build_evidence_surface_supportability(
    features: list[dict[str, str | bool]], workflows: list[dict[str, str | bool]]
) -> dict[str, str | int]:
    feature_map = {str(feature["key"]): feature for feature in features}
    evidence_features = [
        feature_map[key] for key in sorted(EVIDENCE_SURFACE_FEATURE_KEYS) if key in feature_map
    ]
    evidence_feature_count = len(evidence_features)
    ready_evidence_feature_count = sum(1 for feature in evidence_features if feature["enabled"])
    degraded_evidence_feature_count = evidence_feature_count - ready_evidence_feature_count
    workflow_count = len(workflows)
    ready_workflow_count = sum(1 for workflow in workflows if workflow["enabled"])

    if evidence_feature_count == 0:
        state = "empty"
        reason = "evidence_surface_empty"
        freshness_bucket = "unknown"
    elif degraded_evidence_feature_count or ready_workflow_count != workflow_count:
        state = "degraded"
        reason = "evidence_surface_degraded"
        freshness_bucket = "unknown"
    else:
        state = "ready"
        reason = "evidence_surface_ready"
        freshness_bucket = "current"

    record_evidence_surface_supportability(
        state=state,
        reason=reason,
        freshness_bucket=freshness_bucket,
    )
    return {
        "state": state,
        "reason": reason,
        "freshness_bucket": freshness_bucket,
        "evidence_feature_count": evidence_feature_count,
        "ready_evidence_feature_count": ready_evidence_feature_count,
        "degraded_evidence_feature_count": degraded_evidence_feature_count,
        "workflow_count": workflow_count,
        "ready_workflow_count": ready_workflow_count,
    }
