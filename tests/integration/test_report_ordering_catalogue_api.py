from fastapi.testclient import TestClient

from app.main import app
from app.report_ordering_catalogue.models import ReportOrderingCatalogueResponse
from app.report_ordering_catalogue.router import get_report_ordering_catalogue_service

client = TestClient(app)
app.state.report_job_ledger_readiness_override = lambda: True
app.state.report_input_snapshot_store_readiness_override = lambda: True


class _CatalogueService:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str | None]] = []

    async def get_catalogue(
        self,
        *,
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> ReportOrderingCatalogueResponse:
        self.calls.append((correlation_id, trace_id))
        return ReportOrderingCatalogueResponse(
            report_families=[],
            supportability={
                "state": "unavailable",
                "reason_code": "report_catalogue_unavailable",
                "message": "No published report family is currently available.",
            },
        )


def test_report_ordering_catalogue_returns_typed_contract_and_propagates_context() -> None:
    service = _CatalogueService()
    app.dependency_overrides[get_report_ordering_catalogue_service] = lambda: service
    try:
        response = client.get(
            "/integration/report-ordering-catalogue",
            headers={
                "X-Correlation-ID": "corr-catalogue-api",
                "X-Trace-ID": "0123456789abcdef0123456789abcdef",
            },
        )
    finally:
        app.dependency_overrides.pop(get_report_ordering_catalogue_service, None)

    assert response.status_code == 200
    assert response.json() == {
        "source_service": "lotus-report",
        "contract_version": "report-ordering-catalogue.v1",
        "report_families": [],
        "supportability": {
            "state": "unavailable",
            "reason_code": "report_catalogue_unavailable",
            "message": "No published report family is currently available.",
        },
    }
    assert service.calls == [("corr-catalogue-api", "0123456789abcdef0123456789abcdef")]


def test_report_ordering_catalogue_openapi_is_product_safe_and_typed() -> None:
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/integration/report-ordering-catalogue"]["get"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    example = operation["responses"]["200"]["content"]["application/json"]["example"]

    assert response_schema == {"$ref": "#/components/schemas/ReportOrderingCatalogueResponse"}
    assert operation["summary"] == "Get report ordering catalogue"
    assert example["contract_version"] == "report-ordering-catalogue.v1"
    assert example["report_families"][0]["business_label"] == "Portfolio review report"
    assert "template_id" not in str(example)
    assert "client distribution" not in str(example).lower()
    assert (
        schema["components"]["schemas"]["ReportConfigurationField"]["additionalProperties"] is False
    )
