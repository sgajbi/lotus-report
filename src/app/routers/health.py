from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    summary="Service health",
    description="Liveness endpoint for reporting and aggregation service.",
)
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/health/ready",
    summary="Service readiness",
    description="Readiness endpoint for orchestration and integration checks.",
)
def ready() -> dict[str, str]:
    return {"status": "ready"}
