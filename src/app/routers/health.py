from fastapi import APIRouter, Request, Response, status

from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.service import get_report_input_snapshot_store

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    summary="Service health",
    description="Liveness endpoint for reporting and aggregation service.",
)
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/health/live",
    summary="Service liveness",
    description="Liveness endpoint for orchestration and runtime checks.",
)
def live() -> dict[str, str]:
    return {"status": "live"}


@router.get(
    "/health/ready",
    summary="Service readiness",
    description="Readiness endpoint for orchestration and integration checks.",
)
def ready(request: Request, response: Response) -> dict[str, str]:
    if bool(getattr(request.app.state, "is_draining", False)):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "draining"}
    job_readiness_override = getattr(
        request.app.state, "report_job_ledger_readiness_override", None
    )
    snapshot_readiness_override = getattr(
        request.app.state, "report_input_snapshot_store_readiness_override", None
    )
    if callable(job_readiness_override) or callable(snapshot_readiness_override):
        job_ready = bool(job_readiness_override()) if callable(job_readiness_override) else True
        snapshot_ready = (
            bool(snapshot_readiness_override()) if callable(snapshot_readiness_override) else True
        )
        if job_ready and snapshot_ready:
            return {"status": "ready"}
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        reason = (
            "report_job_ledger_unavailable"
            if not job_ready
            else "report_input_snapshot_store_unavailable"
        )
        return {"status": "not_ready", "reason": reason}
    try:
        get_report_job_ledger().check_ready()
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "reason": "report_job_ledger_unavailable"}
    try:
        get_report_input_snapshot_store().check_ready()
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "reason": "report_input_snapshot_store_unavailable"}
    return {"status": "ready"}
