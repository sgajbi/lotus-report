from fastapi import FastAPI

from app.routers.aggregations import router as aggregations_router
from app.routers.health import router as health_router
from app.routers.reports import router as reports_router

app = FastAPI(
    title="Reporting and Aggregation Service",
    version="0.1.0",
    description=(
        "Generates reporting-ready aggregated views from PAS core data and PA analytics outputs."
    ),
    openapi_tags=[
        {"name": "Health", "description": "Service health and readiness endpoints."},
        {"name": "Aggregations", "description": "Aggregated portfolio and analytics read models."},
        {"name": "Reports", "description": "Report-generation APIs and report metadata."},
    ],
)

app.include_router(health_router)
app.include_router(aggregations_router)
app.include_router(reports_router)
