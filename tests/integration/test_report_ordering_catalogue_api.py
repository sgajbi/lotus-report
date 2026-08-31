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


class _AvailabilityLookupStub:
    def __init__(self, status_code, payload):
        self._status_code = status_code
        self._payload = payload
        self.calls = []

    async def get_latest_accepted_brief(self, **kwargs):
        self.calls.append(kwargs)
        return self._status_code, self._payload


def _install_availability_lookup(stub):
    from app.report_ordering_catalogue.router import get_advisor_brief_lookup_client

    app.dependency_overrides[get_advisor_brief_lookup_client] = lambda: stub
    return get_advisor_brief_lookup_client


def test_advisor_commentary_availability_ready_carries_order_identity():
    """Issue #166 acceptance 2: an accepted brief makes the section ready and
    hands the ordering flow the run id the order must carry."""

    stub = _AvailabilityLookupStub(
        200,
        {
            "run_id": "wfr-accepted-001",
            "content_hash": "b" * 64,
            "context": {
                "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                "period": "YTD",
                "as_of_date": "2026-04-22",
                "reporting_currency": "USD",
            },
            "review": {
                "reviewed_by": "banker.sg.301",
                "reviewed_at": "2026-08-30T09:05:00Z",
            },
        },
    )
    override_key = _install_availability_lookup(stub)
    try:
        response = TestClient(app).get(
            "/integration/report-ordering-catalogue/advisor-commentary-availability",
            params={
                "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                "as_of_date": "2026-04-22",
                "reporting_currency": "USD",
            },
            headers={"X-Tenant-Id": "tenant-sg-001"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["contract_version"] == "advisor-commentary-availability.v1"
        assert body["section_id"] == "ADVISOR_COMMENTARY"
        assert body["state"] == "ready"
        assert body["reason_code"] == "advisor_brief_accepted"
        assert body["accepted_brief"]["run_id"] == "wfr-accepted-001"
        assert body["accepted_brief"]["reviewed_by"] == "banker.sg.301"
        assert stub.calls == [
            {
                "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                "tenant_id": "tenant-sg-001",
                "as_of_date": "2026-04-22",
                "reporting_currency": "USD",
            }
        ]
    finally:
        app.dependency_overrides.pop(override_key, None)


def test_advisor_commentary_availability_reports_bounded_unavailable_reasons():
    for lookup_payload, expected_reason in [
        ({"metadata": {"reason_code": "no_accepted_run"}}, "advisor_brief_not_reviewed"),
        ({"metadata": {"reason_code": "no_context_match"}}, "advisor_brief_context_mismatch"),
    ]:
        stub = _AvailabilityLookupStub(404, lookup_payload)
        override_key = _install_availability_lookup(stub)
        try:
            response = TestClient(app).get(
                "/integration/report-ordering-catalogue/advisor-commentary-availability",
                params={"portfolio_id": "PB_SG_GLOBAL_BAL_001"},
                headers={"X-Tenant-Id": "tenant-sg-001"},
            )

            assert response.status_code == 200
            body = response.json()
            assert body["state"] == "unavailable"
            assert body["reason_code"] == expected_reason
            assert body["accepted_brief"] is None
        finally:
            app.dependency_overrides.pop(override_key, None)


def test_advisor_commentary_availability_requires_tenant_and_portfolio():
    stub = _AvailabilityLookupStub(200, {})
    override_key = _install_availability_lookup(stub)
    try:
        missing_tenant = TestClient(app).get(
            "/integration/report-ordering-catalogue/advisor-commentary-availability",
            params={"portfolio_id": "PB_SG_GLOBAL_BAL_001"},
        )
        missing_portfolio = TestClient(app).get(
            "/integration/report-ordering-catalogue/advisor-commentary-availability",
            headers={"X-Tenant-Id": "tenant-sg-001"},
        )

        assert missing_tenant.status_code == 422
        assert missing_portfolio.status_code == 422
        assert stub.calls == []
    finally:
        app.dependency_overrides.pop(override_key, None)
