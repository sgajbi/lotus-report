import json
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.routers import health as health_router
from app.routers.reports import get_reporting_read_service

client = TestClient(app)
app.state.report_job_ledger_readiness_override = lambda: True


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers.get("X-Correlation-Id")
    assert response.headers.get("X-Request-Id")
    assert response.headers.get("X-Trace-Id")


def test_health_live_and_ready():
    live = client.get("/health/live")
    ready = client.get("/health/ready")
    assert live.status_code == 200
    assert ready.status_code == 200
    assert live.json() == {"status": "live"}
    assert ready.json() == {"status": "ready"}


def test_health_ready_returns_503_when_draining():
    app.state.is_draining = True
    response = client.get("/health/ready")
    app.state.is_draining = False

    assert response.status_code == 503
    assert response.json() == {"status": "draining"}


def test_health_ready_uses_report_job_ledger_readiness_when_no_override(monkeypatch):
    class _ReadyLedger:
        def check_ready(self) -> None:
            return None

    previous_override = getattr(app.state, "report_job_ledger_readiness_override", None)
    delattr(app.state, "report_job_ledger_readiness_override")
    monkeypatch.setattr(health_router, "get_report_job_ledger", lambda: _ReadyLedger())
    try:
        response = client.get("/health/ready")
    finally:
        app.state.report_job_ledger_readiness_override = previous_override

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_health_ready_reports_unavailable_when_readiness_override_fails():
    previous_override = getattr(app.state, "report_job_ledger_readiness_override", None)
    app.state.report_job_ledger_readiness_override = lambda: False
    try:
        response = client.get("/health/ready")
    finally:
        app.state.report_job_ledger_readiness_override = previous_override

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "reason": "report_job_ledger_unavailable",
    }


def test_health_ready_reports_unavailable_when_report_job_ledger_check_fails(monkeypatch):
    class _UnavailableLedger:
        def check_ready(self) -> None:
            raise RuntimeError("schema unavailable")

    previous_override = getattr(app.state, "report_job_ledger_readiness_override", None)
    delattr(app.state, "report_job_ledger_readiness_override")
    monkeypatch.setattr(health_router, "get_report_job_ledger", lambda: _UnavailableLedger())
    try:
        response = client.get("/health/ready")
    finally:
        app.state.report_job_ledger_readiness_override = previous_override

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "reason": "report_job_ledger_unavailable",
    }


def test_lifespan_sets_drain_flag_on_shutdown():
    with TestClient(app) as local_client:
        assert app.state.is_draining is False
        response = local_client.get("/health/ready")
        assert response.status_code == 200

    assert app.state.is_draining is True
    app.state.is_draining = False


def test_metrics_endpoint_available():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text or "http_request_duration" in response.text


def test_load_concurrency_health_live_requests():
    def call_live() -> int:
        return client.get("/health/live").status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: call_live(), range(32)))

    assert all(status == 200 for status in statuses)


def test_load_concurrency_health_ready_requests():
    def call_ready() -> int:
        return client.get("/health/ready").status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: call_ready(), range(32)))

    assert all(status == 200 for status in statuses)


def test_load_concurrency_metrics_requests():
    def call_metrics() -> int:
        return client.get("/metrics").status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _: call_metrics(), range(24)))

    assert all(status == 200 for status in statuses)


def test_integration_capabilities():
    response = client.get(
        "/integration/capabilities?consumer_system=lotus-gateway&tenant_id=default"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_service"] == "lotus-report"
    assert body["contract_version"] == "v1"
    assert body["policy_version"] == "ras-default-v1"
    assert body["supported_input_modes"] == ["portfolio_id"]
    feature_keys = {feature["key"] for feature in body["features"] if feature["enabled"]}
    assert {
        "lotus-report.reporting.portfolio_review.first_class.v1",
        "lotus-report.reporting.portfolio_review.section_readiness.v1",
        "lotus-report.reporting.portfolio_review.evidence_pack.v1",
        "lotus-report.reporting.portfolio_review.key_figures.v1",
        "lotus-report.reporting.portfolio_review.position_pnl.v1",
        "lotus-report.reporting.portfolio_review.performance_contribution.v1",
        "lotus-report.reporting.portfolio_review.source_backed_risk_free.v1",
        "lotus-report.reporting.portfolio_review.source_backed_benchmark.v1",
        "lotus-report.reporting.portfolio_review.transaction_realized_pnl.v1",
        "lotus-report.reporting.portfolio_review.client_profile.v1",
        "lotus-report.reporting.portfolio_review.advisor_briefing.v1",
        "lotus-report.reporting.portfolio_review.ai_readiness.v1",
        "lotus-report.reporting.portfolio_review.upstream_capability_audit.v1",
        "lotus-report.reporting.portfolio_review.advisor_sections.v1",
        "lotus-report.reporting.portfolio_review.workbench_ready.v1",
        "lotus-report.reporting.portfolio_review.job_ledger.v1",
        "lotus-report.reporting.portfolio_review.idempotent_job_create.v1",
        "lotus-report.reporting.portfolio_review.job_status.v1",
        "lotus-report.reporting.portfolio_review.job_event_history.v1",
        "lotus-report.reporting.portfolio_review.pre_render_cancel.v1",
    } <= feature_keys
    workflow_keys = {workflow["workflow_key"] for workflow in body["workflows"]}
    assert "portfolio_review_report_job" in workflow_keys


def test_integration_capabilities_camel_case_params_do_not_override_context():
    response = client.get("/integration/capabilities?consumerSystem=lotus-manage&tenantId=tenant-x")

    assert response.status_code == 200
    body = response.json()
    assert body["source_service"] == "lotus-report"
    assert body["contract_version"] == "v1"
    assert body["policy_version"] == "ras-default-v1"


def test_aggregation_endpoint():
    response = client.get(
        "/aggregations/portfolios/DEMO_DPM_EUR_001?as_of_date=2026-02-24&live=false"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["scope"]["portfolio_id"] == "DEMO_DPM_EUR_001"
    assert len(body["rows"]) >= 1


def test_stale_generic_report_endpoint_is_not_exposed():
    response = client.post("/reports", json={})
    assert response.status_code == 404


class _StubReportingReadService:
    async def get_portfolio_summary(
        self, portfolio_id: str, request_payload: dict, correlation_id: str | None
    ) -> dict:
        scope = {
            "portfolio_id": portfolio_id,
            "as_of_date": request_payload.get("as_of_date"),
        }
        return {
            "scope": scope,
            "wealth": {"total_market_value": 1_000_000.0, "total_cash": 50_000.0},
        }

    async def get_portfolio_review(
        self, portfolio_id: str, request_payload: dict, correlation_id: str | None
    ) -> dict:
        return {
            "contract_version": "v1",
            "report_id": f"portfolio-review:{portfolio_id}:{request_payload.get('as_of_date')}",
            "portfolio_id": portfolio_id,
            "as_of_date": request_payload.get("as_of_date"),
            "generated_at": "2026-04-22T09:00:00Z",
            "readiness": {"status": "ready"},
            "overview": {"total_market_value": 1_000_000.0, "total_cash": 50_000.0},
        }


class _StubReportingReadServiceFailure:
    async def get_portfolio_summary(
        self, portfolio_id: str, request_payload: dict, correlation_id: str | None
    ) -> dict:
        raise HTTPException(status_code=422, detail="Missing required request field: as_of_date")

    async def get_portfolio_review(
        self, portfolio_id: str, request_payload: dict, correlation_id: str | None
    ) -> dict:
        raise HTTPException(status_code=502, detail="lotus-core upstream failure")


def test_ras_portfolio_summary_endpoint():
    app.dependency_overrides[get_reporting_read_service] = lambda: _StubReportingReadService()
    response = client.post(
        "/reports/portfolios/DEMO_DPM_EUR_001/summary",
        json={
            "as_of_date": "2026-02-24",
            "period": {"type": "YTD"},
            "sections": ["WEALTH", "ALLOCATION"],
            "allocation_dimensions": ["ASSET_CLASS"],
        },
    )
    app.dependency_overrides.pop(get_reporting_read_service, None)

    assert response.status_code == 200
    body = response.json()
    assert body["scope"]["portfolio_id"] == "DEMO_DPM_EUR_001"
    assert body["wealth"]["total_market_value"] == 1_000_000.0


def test_ras_portfolio_summary_propagates_validation_error():
    app.dependency_overrides[get_reporting_read_service] = lambda: (
        _StubReportingReadServiceFailure()
    )
    response = client.post(
        "/reports/portfolios/DEMO_DPM_EUR_001/summary",
        json={},
    )
    app.dependency_overrides.pop(get_reporting_read_service, None)

    assert response.status_code == 422
    assert "Missing required request field" in response.json()["detail"]


def test_ras_portfolio_review_endpoint():
    app.dependency_overrides[get_reporting_read_service] = lambda: _StubReportingReadService()
    response = client.post(
        "/reports/portfolios/DEMO_DPM_EUR_001/review",
        json={
            "as_of_date": "2026-02-24",
            "sections": ["OVERVIEW", "ALLOCATION", "HOLDINGS"],
        },
    )
    app.dependency_overrides.pop(get_reporting_read_service, None)

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "v1"
    assert body["report_id"] == "portfolio-review:DEMO_DPM_EUR_001:2026-02-24"
    assert body["portfolio_id"] == "DEMO_DPM_EUR_001"
    assert body["as_of_date"] == "2026-02-24"
    assert body["readiness"] == {"status": "ready", "reason": None}
    assert body["overview"]["total_market_value"] == 1_000_000.0


def test_ras_portfolio_review_rejects_camel_case_request_alias():
    app.dependency_overrides[get_reporting_read_service] = lambda: _StubReportingReadService()
    response = client.post(
        "/reports/portfolios/DEMO_DPM_EUR_001/review",
        json={
            "asOfDate": "2026-02-24",
            "sections": ["OVERVIEW"],
        },
    )
    app.dependency_overrides.pop(get_reporting_read_service, None)

    assert response.status_code == 422


def test_openapi_uses_typed_portfolio_review_contract():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    review_post = schema["paths"]["/reports/portfolios/{portfolio_id}/review"]["post"]
    response_schema = review_post["responses"]["200"]["content"]["application/json"]["schema"]
    request_schema = review_post["requestBody"]["content"]["application/json"]["schema"]
    request_content = review_post["requestBody"]["content"]["application/json"]
    response_content = review_post["responses"]["200"]["content"]["application/json"]

    serialized_schema = json.dumps(schema)

    assert review_post["summary"] == "Get portfolio review report"
    assert "machine-readable JSON" in review_post["description"]
    assert "not sourced" in review_post["description"]
    assert "RFC-" not in serialized_schema
    assert "RFC" not in serialized_schema
    assert "first-class" not in serialized_schema.lower()
    assert "/reports" not in schema["paths"]

    assert response_schema["$ref"].endswith("/PortfolioReviewReportResponse")
    assert request_schema["$ref"].endswith("/PortfolioReviewReportRequest")
    request_contract = schema["components"]["schemas"]["PortfolioReviewReportRequest"]
    assert "benchmark_code" in request_contract["properties"]
    assert "benchmarkCode" not in request_contract["properties"]
    assert request_contract["properties"]["benchmark_code"]["description"]
    assert request_content["example"]["look_through_mode"] == "direct_only"
    assert (
        request_content["examples"]["full_portfolio_review"]["value"]["sections"]
        == (request_content["example"]["sections"])
    )
    assert "CLIENT_PROFILE" in request_contract["examples"][0]["sections"]

    response_contract = schema["components"]["schemas"]["PortfolioReviewReportResponse"]
    for property_name in [
        "client_profile",
        "key_figures",
        "report_coverage",
        "upstream_capability_audit",
        "advisor_briefing",
        "ai_readiness",
    ]:
        assert response_contract["properties"][property_name]["description"]

    examples = response_contract["examples"]
    assert response_contract["example"]["holdings"]
    assert response_content["example"]["holdings"]
    assert (
        response_content["examples"]["full_portfolio_review"]["value"]["holdings"]
        == (response_content["example"]["holdings"])
    )
    assert examples[0]["client_profile"]["status"] == "present"
    assert examples[0]["holdings"]["holdings_by_asset_class"]["EQUITY"][0]["unrealized_pnl"]
    assert examples[0]["performance"]["contribution"]["by_position"][0]["contribution_pct"]
    assert (
        examples[0]["report_coverage"]["targets_guidelines_and_suitability"]["status"]
        == "not_sourced"
    )
    assert examples[0]["upstream_capability_audit"]["status"] == "action_required"
    assert "trade_recommendation" in examples[0]["ai_readiness"]["blocked_features"]


def test_ras_portfolio_review_propagates_upstream_error():
    app.dependency_overrides[get_reporting_read_service] = lambda: (
        _StubReportingReadServiceFailure()
    )
    response = client.post(
        "/reports/portfolios/DEMO_DPM_EUR_001/review",
        json={"as_of_date": "2026-02-24"},
    )
    app.dependency_overrides.pop(get_reporting_read_service, None)

    assert response.status_code == 502
    assert "upstream failure" in response.json()["detail"]


def test_ras_portfolio_summary_includes_correlation_headers():
    app.dependency_overrides[get_reporting_read_service] = lambda: _StubReportingReadService()
    response = client.post(
        "/reports/portfolios/DEMO_DPM_EUR_001/summary",
        json={
            "as_of_date": "2026-02-24",
            "period": {"type": "YTD"},
        },
        headers={"X-Correlation-Id": "corr-ras-it-001"},
    )
    app.dependency_overrides.pop(get_reporting_read_service, None)

    assert response.status_code == 200
    assert response.headers.get("X-Correlation-Id") == "corr-ras-it-001"
    assert response.headers.get("X-Request-Id")
    assert response.headers.get("X-Trace-Id")


def test_ras_portfolio_summary_rejects_invalid_section_limit():
    app.dependency_overrides[get_reporting_read_service] = lambda: _StubReportingReadService()
    response = client.post(
        "/reports/portfolios/DEMO_DPM_EUR_001/summary?section_limit=0",
        json={"as_of_date": "2026-02-24", "sections": ["WEALTH"]},
    )
    app.dependency_overrides.pop(get_reporting_read_service, None)

    assert response.status_code == 422
