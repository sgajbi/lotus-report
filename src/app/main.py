from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.enterprise_readiness import (
    build_enterprise_audit_middleware,
    validate_enterprise_runtime_config,
)
from app.observability import setup_observability
from app.routers.aggregations import router as aggregations_router
from app.routers.health import router as health_router
from app.routers.integration import router as integration_router
from app.routers.report_batches import router as report_batches_router
from app.routers.report_batches import schedules_router as report_batch_schedules_router
from app.routers.report_jobs import evidence_router as report_evidence_router
from app.routers.report_jobs import jobs_router as report_jobs_router
from app.routers.report_jobs import router as report_job_submission_router
from app.routers.reporting_operations import router as reporting_operations_router
from app.routers.reports import router as reports_router


@asynccontextmanager
async def _app_lifespan(application: FastAPI) -> AsyncIterator[None]:
    application.state.is_draining = False
    yield
    application.state.is_draining = True


app = FastAPI(
    title="Reporting and Aggregation Service",
    version="0.1.0",
    description=(
        "Generates reporting-ready aggregated views from "
        "lotus-core core data and lotus-performance analytics "
        "outputs."
    ),
    openapi_tags=[
        {"name": "Health", "description": "Service health and readiness endpoints."},
        {"name": "Integration", "description": "Cross-service integration contracts."},
        {"name": "Aggregations", "description": "Aggregated portfolio and analytics read models."},
        {
            "name": "Reports",
            "description": "Report data and report-command APIs for product consumers.",
        },
        {
            "name": "Report Jobs",
            "description": "Operational report-job lifecycle APIs for support and diagnostics.",
        },
        {
            "name": "Report Batches",
            "description": "Operational report-batch materialization, status, and control APIs.",
        },
        {
            "name": "Report Batch Schedules",
            "description": "Governed report-batch scheduler configuration and run APIs.",
        },
        {
            "name": "Report Evidence",
            "description": "Support-safe snapshot and upstream-lineage evidence APIs.",
        },
        {
            "name": "Report Operations",
            "description": "Source-backed reporting operations attention and SLA APIs.",
        },
    ],
    lifespan=_app_lifespan,
)
setup_observability(app)
validate_enterprise_runtime_config()
app.middleware("http")(build_enterprise_audit_middleware())

app.include_router(health_router)
app.include_router(integration_router)
app.include_router(aggregations_router)
app.include_router(report_job_submission_router)
app.include_router(reports_router)
app.include_router(report_batches_router)
app.include_router(report_batch_schedules_router)
app.include_router(report_jobs_router)
app.include_router(report_evidence_router)
app.include_router(reporting_operations_router)
