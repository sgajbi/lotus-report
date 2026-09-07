import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.application_errors import ReportingUpstreamError, ReportingValidationError
from app.main import app
from app.routers import health as health_router
from app.routers.reports import get_reporting_read_service

client = TestClient(app)
app.state.report_job_ledger_readiness_override = lambda: True
app.state.report_input_snapshot_store_readiness_override = lambda: True


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers.get("X-Correlation-Id")
    assert response.headers.get("X-Request-Id")
    assert response.headers.get("X-Trace-Id")


def test_health_omits_traceparent_for_non_w3c_trace_id():
    response = client.get("/health", headers={"X-Trace-Id": "trace-human-readable"})

    assert response.status_code == 200
    assert response.headers.get("X-Trace-Id") == "trace-human-readable"
    assert "traceparent" not in response.headers


def test_health_emits_traceparent_for_valid_w3c_trace_id():
    trace_id = "0123456789abcdef0123456789abcdef"
    response = client.get("/health", headers={"X-Trace-Id": trace_id})

    assert response.status_code == 200
    assert response.headers.get("traceparent") == f"00-{trace_id}-0000000000000001-01"


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

    class _ReadySnapshotStore:
        def check_ready(self) -> None:
            return None

    previous_override = getattr(app.state, "report_job_ledger_readiness_override", None)
    previous_snapshot_override = getattr(
        app.state, "report_input_snapshot_store_readiness_override", None
    )
    delattr(app.state, "report_job_ledger_readiness_override")
    delattr(app.state, "report_input_snapshot_store_readiness_override")
    monkeypatch.setattr(health_router, "get_report_job_ledger", lambda: _ReadyLedger())
    monkeypatch.setattr(
        health_router, "get_report_input_snapshot_store", lambda: _ReadySnapshotStore()
    )
    try:
        response = client.get("/health/ready")
    finally:
        app.state.report_job_ledger_readiness_override = previous_override
        app.state.report_input_snapshot_store_readiness_override = previous_snapshot_override

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_health_ready_reports_unavailable_when_readiness_override_fails():
    previous_override = getattr(app.state, "report_job_ledger_readiness_override", None)
    previous_snapshot_override = getattr(
        app.state, "report_input_snapshot_store_readiness_override", None
    )
    app.state.report_job_ledger_readiness_override = lambda: False
    app.state.report_input_snapshot_store_readiness_override = lambda: True
    try:
        response = client.get("/health/ready")
    finally:
        app.state.report_job_ledger_readiness_override = previous_override
        app.state.report_input_snapshot_store_readiness_override = previous_snapshot_override

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "reason": "report_job_ledger_unavailable",
    }


def test_health_ready_reports_unavailable_when_report_job_ledger_check_fails(monkeypatch):
    class _UnavailableLedger:
        def check_ready(self) -> None:
            raise RuntimeError("schema unavailable")

    class _ReadySnapshotStore:
        def check_ready(self) -> None:
            return None

    previous_override = getattr(app.state, "report_job_ledger_readiness_override", None)
    previous_snapshot_override = getattr(
        app.state, "report_input_snapshot_store_readiness_override", None
    )
    delattr(app.state, "report_job_ledger_readiness_override")
    delattr(app.state, "report_input_snapshot_store_readiness_override")
    monkeypatch.setattr(health_router, "get_report_job_ledger", lambda: _UnavailableLedger())
    monkeypatch.setattr(
        health_router, "get_report_input_snapshot_store", lambda: _ReadySnapshotStore()
    )
    try:
        response = client.get("/health/ready")
    finally:
        app.state.report_job_ledger_readiness_override = previous_override
        app.state.report_input_snapshot_store_readiness_override = previous_snapshot_override

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "reason": "report_job_ledger_unavailable",
    }


def test_health_ready_reports_unavailable_when_snapshot_store_check_fails(monkeypatch):
    class _ReadyLedger:
        def check_ready(self) -> None:
            return None

    class _UnavailableSnapshotStore:
        def check_ready(self) -> None:
            raise RuntimeError("snapshot store unavailable")

    previous_override = getattr(app.state, "report_job_ledger_readiness_override", None)
    previous_snapshot_override = getattr(
        app.state, "report_input_snapshot_store_readiness_override", None
    )
    delattr(app.state, "report_job_ledger_readiness_override")
    delattr(app.state, "report_input_snapshot_store_readiness_override")
    monkeypatch.setattr(health_router, "get_report_job_ledger", lambda: _ReadyLedger())
    monkeypatch.setattr(
        health_router,
        "get_report_input_snapshot_store",
        lambda: _UnavailableSnapshotStore(),
    )
    try:
        response = client.get("/health/ready")
    finally:
        app.state.report_job_ledger_readiness_override = previous_override
        app.state.report_input_snapshot_store_readiness_override = previous_snapshot_override

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "reason": "report_input_snapshot_store_unavailable",
    }


def test_health_ready_reports_unavailable_when_snapshot_store_override_fails():
    previous_override = getattr(app.state, "report_job_ledger_readiness_override", None)
    previous_snapshot_override = getattr(
        app.state, "report_input_snapshot_store_readiness_override", None
    )
    app.state.report_job_ledger_readiness_override = lambda: True
    app.state.report_input_snapshot_store_readiness_override = lambda: False
    try:
        response = client.get("/health/ready")
    finally:
        app.state.report_job_ledger_readiness_override = previous_override
        app.state.report_input_snapshot_store_readiness_override = previous_snapshot_override

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "reason": "report_input_snapshot_store_unavailable",
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


def test_metrics_endpoint_exposes_reporting_metric_contract():
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "lotus_report_operations_total" in response.text
    assert "lotus_report_operation_duration_seconds" in response.text
    assert "lotus_report_job_work_lease_events_total" in response.text
    assert "lotus_report_replay_operations_total" not in response.text


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
        "lotus-report.reporting.report_ordering_catalogue.v1",
        "lotus-report.reporting.portfolio_review.job_ledger.v1",
        "lotus-report.reporting.portfolio_review.idempotent_job_create.v1",
        "lotus-report.reporting.portfolio_review.job_status.v1",
        "lotus-report.reporting.portfolio_review.job_event_history.v1",
        "lotus-report.reporting.portfolio_review.pre_render_cancel.v1",
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
        "lotus-report.reporting.observability.traceability.v1",
        "lotus-report.reporting.observability.metrics.v1",
        "report.observability.evidence_surface_supportability",
        "lotus-report.reporting.batch_materialization_api.v1",
        "lotus-report.reporting.batch_control_api.v1",
    } <= feature_keys
    workflow_keys = {workflow["workflow_key"] for workflow in body["workflows"]}
    assert "portfolio_review_report_job" in workflow_keys
    assert "report_ordering" in workflow_keys
    assert body["supportability"] == {
        "state": "ready",
        "reason": "evidence_surface_ready",
        "freshness_bucket": "current",
        "evidence_feature_count": 14,
        "ready_evidence_feature_count": 14,
        "degraded_evidence_feature_count": 0,
        "workflow_count": 4,
        "ready_workflow_count": 4,
    }


def test_integration_capabilities_records_bounded_supportability_metric():
    capabilities_response = client.get(
        "/integration/capabilities?consumer_system=lotus-gateway&tenant_id=default"
    )
    metrics_response = client.get("/metrics")

    assert capabilities_response.status_code == 200
    assert metrics_response.status_code == 200
    # The boundedness invariant belongs to the supportability metric itself, so the
    # identifier assertions are scoped to its sample lines. Asserting over the whole
    # exposition was order-dependent: once another test exercises a portfolio route,
    # the HTTP instrumentator's handler label legitimately carries the route template
    # "/reports/portfolios/{portfolio_id}/..." - a bounded template, not a leak.
    supportability_lines = [
        line
        for line in metrics_response.text.splitlines()
        if line.startswith("lotus_report_evidence_surface_supportability_total")
    ]
    assert supportability_lines
    supportability_text = "\n".join(supportability_lines)
    assert 'freshness_bucket="current"' in supportability_text
    assert 'reason="evidence_surface_ready"' in supportability_text
    assert 'state="ready"' in supportability_text
    assert "portfolio_id" not in supportability_text
    assert "client_id" not in supportability_text
    assert "tenant_id" not in supportability_text


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


def test_aggregation_endpoint_refuses_a_malformed_as_of_date():
    """A bad date is a caller error, and must be reported as one.

    The query parameter was declared `str` and described as YYYY-MM-DD without
    being validated as a date, so a malformed value travelled into the service
    and failed inside `AggregationScope` -- a pydantic error raised after the
    request had been accepted, which FastAPI does not convert. The caller got a
    500 for their own bad input, and the response said nothing about which
    parameter was wrong.
    """

    response = client.get(
        "/aggregations/portfolios/DEMO_DPM_EUR_001?as_of_date=not-a-date&live=false"
    )

    assert response.status_code == 422
    # The refusal has to name the parameter, or it is only marginally better
    # than the 500 it replaces.
    assert "as_of_date" in response.text


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
        self,
        portfolio_id: str,
        request_payload: dict,
        correlation_id: str | None,
        admitted_tenant_id: str | None = None,
        evidence_posture: str = "ephemeral_composition",
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
        raise ReportingValidationError("Missing required request field: as_of_date")

    async def get_portfolio_review(
        self,
        portfolio_id: str,
        request_payload: dict,
        correlation_id: str | None,
        admitted_tenant_id: str | None = None,
        evidence_posture: str = "ephemeral_composition",
    ) -> dict:
        raise ReportingUpstreamError("lotus-core upstream failure")


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
    assert request_content["example"]["benchmark_code"] == "BMK_PB_GLOBAL_BALANCED_60_40"
    assert request_content["example"]["look_through_mode"] == "direct_only"
    allocation_description = request_contract["properties"]["allocation_dimensions"]["description"]
    assert "or issuer where" not in allocation_description
    assert "Unsupported issuer dimensions" in allocation_description
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
    assert response_content["example"]["audience"]["client_distribution_allowed"] is False
    assert examples[0]["audience"]["client_distribution_allowed"] is False
    assert '"client_distribution_allowed": true' not in serialized_schema.lower()
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
