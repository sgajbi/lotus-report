"""Configured operator admission, independently of source portfolio admission.

Registered HTTP and native durable adapters; source data and caller assertions are
synthetic. Neither the production profile nor transport headers certify real IAM.
"""

import calendar
import json
import os
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.enterprise_readiness import validate_enterprise_runtime_config
from app.main import app
from app.report_batch_orchestrator.ledger import ReportBatchLedger
from app.report_batch_orchestrator.postgres_ledger import PostgresReportBatchLedger
from app.report_batch_orchestrator.schedule_definitions import ScheduleDefinitionService
from app.report_batch_orchestrator.scheduler import (
    BatchScheduleDefinition,
    BatchSchedulerConfig,
    ReportBatchScheduler,
    batch_scheduler_caller_context,
)
from app.report_batch_orchestrator.scheduler_process import (
    BatchSchedulerProcess,
    BatchSchedulerProcessConfig,
)
from app.report_batch_orchestrator.service import get_report_batch_scheduler
from app.routers.report_batches import (
    get_report_batch_scheduler_config,
    schedule_definition_service_dependency,
)
from tests.integration.postgres_adapter_ownership import own_postgres_adapter

LIST_PATH = "/reports/batch-schedules"
RUN_PATH = "/reports/batch-schedules:run-due"
REFUSAL = {
    "detail": {
        "code": "batch_scheduler_not_found",
        "message": "Report batch scheduler was not found.",
    }
}


class PortfolioSource:
    def __init__(self):
        self.calls = []

    async def get_portfolio_detail(self, portfolio_id, correlation_id=None, *, admitted_tenant_id):
        self.calls.append((portfolio_id, admitted_tenant_id))
        return 200, {"portfolio_id": portfolio_id, "tenant_id": "tenant-owner", "status": "active"}


class TrackedDefinitions(ScheduleDefinitionService):
    def __init__(self, ledger):
        super().__init__(ledger)
        self.reads = 0

    def list_schedules(self, **kwargs):
        self.reads += 1
        return super().list_schedules(**kwargs)

    def due_definitions_for_scheduler(self, **kwargs):
        self.reads += 1
        return super().due_definitions_for_scheduler(**kwargs)


def headers(tenant="tenant-owner", *, capabilities=True):
    result = {
        "X-Actor-Id": "scheduler-operator",
        "X-Caller-Application": "scheduler-review",
        "X-Tenant-Id": tenant,
        "X-Region": "APAC",
        "X-Booking-Center-Code": "SG",
        "X-Role": "system",
        "X-Correlation-ID": "corr-scheduler-admission",
        "X-Trace-ID": "trace-scheduler-admission",
        "X-Service-Identity": "synthetic-scheduler-service",
    }
    if capabilities:
        result["X-Capabilities"] = "reports.read,reports.write"
    return result


def durable_counts(ledger):
    # Read-only infrastructure verification, never financial-row injection.
    tables = (
        "report_batch",
        "report_batch_item",
        "report_batch_schedule_definition",
        "report_batch_schedule_audit",
    )
    with ledger._connect() as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in tables
        )


@pytest.fixture(params=["sqlite", "postgres"])
def scheduler_boundary(request, tmp_path, monkeypatch):
    if request.param == "postgres":
        database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
        if not database_url:
            pytest.skip("REPORT_JOB_LEDGER_DATABASE_URL required for native PostgreSQL proof")
        ledger = own_postgres_adapter(PostgresReportBatchLedger(database_url))
    else:
        ledger = ReportBatchLedger(tmp_path / "scheduler.sqlite3")
    for key, value in {
        "ENTERPRISE_RUNTIME_PROFILE": "production",
        "ENTERPRISE_ENFORCE_AUTHZ": "true",
        "ENTERPRISE_ENFORCE_READ_AUTHZ": "true",
        "ENTERPRISE_PRIMARY_KEY_ID": "synthetic-scheduler-key",
        "ENTERPRISE_CAPABILITY_RULES_JSON": json.dumps(
            {f"GET {LIST_PATH}": "reports.read", f"POST {RUN_PATH}": "reports.write"}
        ),
    }.items():
        monkeypatch.setenv(key, value)
    assert validate_enterprise_runtime_config() == []
    config = BatchSchedulerConfig(
        scheduler_id=f"scheduler-{uuid4().hex}",
        interval_seconds=60,
        tenant_id="tenant-owner",
        region="APAC",
        booking_center_code="SG",
        role="system",
        schedules=(
            BatchScheduleDefinition(
                schedule_id=f"configured-{uuid4().hex}",
                selector_mode="explicit_portfolio_list",
                frequency="monthly",
                as_of_date=date(2026, 4, 30),
                portfolio_ids=["SCHEDULE_PORTFOLIO"],
            ),
        ),
    )
    source = PortfolioSource()
    definitions = TrackedDefinitions(ledger)
    scheduler = ReportBatchScheduler(
        batch_ledger=ledger, portfolio_source=source, stored_schedule_source=definitions
    )
    app.dependency_overrides[get_report_batch_scheduler] = lambda: scheduler
    app.dependency_overrides[get_report_batch_scheduler_config] = lambda: config
    app.dependency_overrides[schedule_definition_service_dependency] = lambda: definitions
    try:
        with TestClient(app) as client:
            yield client, ledger, config, source, definitions, scheduler
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("method,path", [("GET", LIST_PATH), ("POST", RUN_PATH)])
def test_foreign_configured_scheduler_refuses_before_any_effect(
    scheduler_boundary, method, path, monkeypatch, caplog
):
    client, ledger, config, source, definitions, _ = scheduler_boundary

    def forbidden_ledger_effect(*args, **kwargs):
        pytest.fail("foreign caller reached scheduler ledger lookup/write")

    for operation in (
        "has_batch_for_idempotency_key",
        "has_batch_for_schedule_cycle",
        "create_batch",
    ):
        monkeypatch.setattr(ledger, operation, forbidden_ledger_effect)
    before = durable_counts(ledger)
    pressure = ledger.batch_pressure_snapshot()
    for pass_sequence in (7, 8):
        response = client.request(
            method,
            path,
            headers=headers("tenant-foreign"),
            **({"json": {"pass_sequence": pass_sequence}} if method == "POST" else {}),
        )
        assert response.status_code == 404, response.text
        assert response.json() == REFUSAL
        assert config.scheduler_id not in response.text
        assert config.tenant_id not in response.text
        assert source.calls == []
        assert definitions.reads == 0
        assert durable_counts(ledger) == before
        assert ledger.batch_pressure_snapshot() == pressure
    audits = [record.audit for record in caplog.records if hasattr(record, "audit")]
    assert all(audit["tenant_id"] == "tenant-foreign" for audit in audits)
    if method == "POST":
        assert len(audits) == 2
        assert all(audit["metadata"]["status_code"] == 404 for audit in audits)


def test_owner_stored_and_configured_cycles_keep_identity(scheduler_boundary):
    client, ledger, config, source, definitions, _ = scheduler_boundary
    stored = client.post(
        LIST_PATH,
        headers=headers(),
        json={"cadence": "monthly_end", "portfolio_ids": [f"STORED_{uuid4().hex}"]},
    )
    assert stored.status_code == 201, stored.text
    schedule_id = stored.json()["schedule_id"]
    listing = client.get(LIST_PATH, headers=headers())
    assert listing.status_code == 200
    assert listing.json()["tenant_id"] == config.tenant_id
    assert listing.json()["schedule_count"] == 1
    assert schedule_id in [entry["schedule_id"] for entry in listing.json()["defined_schedules"]]
    assert (
        client.get(f"{LIST_PATH}/{schedule_id}", headers=headers("tenant-foreign")).status_code
        == 404
    )
    today = datetime.now(UTC).date()
    evaluation_date = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    first = client.post(
        RUN_PATH,
        headers=headers(),
        json={"pass_sequence": 1, "evaluation_date": evaluation_date.isoformat()},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["materialized_count"] == 2
    assert body["refused_schedule_ids"] == []
    batch_ids = [entry["batch_id"] for entry in body["materialized"]]
    for batch_id in batch_ids:
        record = ledger.get_batch(batch_id)
        assert record.tenant_id == config.tenant_id
        assert record.booking_center_code == config.booking_center_code
        assert record.correlation_id == body["correlation_id"]
        assert record.item_count == 1
    before = durable_counts(ledger)
    repeated = client.post(
        RUN_PATH,
        headers=headers(),
        json={"pass_sequence": 2, "evaluation_date": evaluation_date.isoformat()},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["materialized_count"] == 1
    assert repeated.json()["skipped_schedule_ids"] == [config.schedules[0].schedule_id]
    stored_first = next(
        entry for entry in body["materialized"] if entry["schedule_id"] == schedule_id
    )
    assert repeated.json()["materialized"][0] == stored_first
    assert durable_counts(ledger) == before
    assert all(tenant == config.tenant_id for _, tenant in source.calls)
    assert definitions.reads > 0


@pytest.mark.parametrize(
    "scope_override",
    [{"X-Region": "EMEA"}, {"X-Booking-Center-Code": "HK"}, {"X-Booking-Center-Code": ""}],
)
def test_same_tenant_foreign_scheduler_scope_is_refused(scheduler_boundary, scope_override):
    client, ledger, _, source, definitions, _ = scheduler_boundary
    before = durable_counts(ledger)
    for method, path in (("GET", LIST_PATH), ("POST", RUN_PATH)):
        response = client.request(
            method,
            path,
            headers={**headers(), **scope_override},
            **({"json": {"pass_sequence": 3}} if method == "POST" else {}),
        )
        assert response.status_code == 404, response.text
        assert response.json() == REFUSAL
    assert source.calls == []
    assert definitions.reads == 0
    assert durable_counts(ledger) == before


@pytest.mark.parametrize("tenant", ["tenant-owner", "tenant-foreign"])
def test_missing_capability_keeps_scheduler_quiet(scheduler_boundary, tenant):
    client, ledger, _, source, definitions, _ = scheduler_boundary
    before = durable_counts(ledger)
    for method, path, capability in (("GET", LIST_PATH, "read"), ("POST", RUN_PATH, "write")):
        response = client.request(
            method,
            path,
            headers=headers(tenant, capabilities=False),
            **({"json": {"pass_sequence": 3}} if method == "POST" else {}),
        )
        assert response.status_code == 403, response.text
        assert f"missing_capability:reports.{capability}" in response.text
    assert source.calls == []
    assert definitions.reads == 0
    assert durable_counts(ledger) == before


@pytest.mark.parametrize("method,path", [("GET", LIST_PATH), ("POST", RUN_PATH)])
@pytest.mark.parametrize(
    "scope_override",
    [
        {"X-Tenant-Id": "tenant-foreign"},
        {"X-Region": "EMEA"},
        {"X-Booking-Center-Code": "HK"},
        {"X-Booking-Center-Code": ""},
    ],
)
def test_foreign_refusal_precedes_dependency_construction(
    scheduler_boundary, method, path, scope_override
):
    client, _, _, _, _, _ = scheduler_boundary
    factory_calls = []

    def forbidden_factory():
        factory_calls.append("constructed")
        raise RuntimeError(
            "foreign caller constructed a scheduler or ledger-backed definition service"
        )

    app.dependency_overrides[get_report_batch_scheduler] = forbidden_factory
    app.dependency_overrides[schedule_definition_service_dependency] = forbidden_factory
    response = client.request(
        method,
        path,
        headers={**headers(), **scope_override},
        **({"json": {"pass_sequence": 5}} if method == "POST" else {}),
    )
    assert response.status_code == 404, response.text
    assert response.json() == REFUSAL
    assert factory_calls == []


@pytest.mark.parametrize("method,path", [("GET", LIST_PATH), ("POST", RUN_PATH)])
@pytest.mark.parametrize("raw", ["{broken", '[{"schedule_id":"broken"}]'])
def test_foreign_scope_refuses_before_schedule_config_parsing(
    scheduler_boundary, method, path, raw, monkeypatch
):
    client, ledger, config, source, definitions, _ = scheduler_boundary
    app.dependency_overrides.pop(get_report_batch_scheduler_config)
    monkeypatch.setattr(settings, "batch_scheduler_tenant_id", config.tenant_id)
    monkeypatch.setattr(settings, "batch_scheduler_region", config.region)
    monkeypatch.setattr(settings, "batch_scheduler_booking_center_code", config.booking_center_code)
    monkeypatch.setattr(settings, "batch_schedules_json", raw)
    before = durable_counts(ledger)
    for scope_override in (
        {"X-Tenant-Id": "tenant-foreign"},
        {"X-Region": "EMEA"},
        {"X-Booking-Center-Code": "HK"},
    ):
        response = client.request(
            method,
            path,
            headers={**headers(), **scope_override},
            **({"json": {"pass_sequence": 5}} if method == "POST" else {}),
        )
        assert response.status_code == 404, response.text
        assert response.json() == REFUSAL
    # The admitted owner retains the existing safe configuration-health error.
    owner = client.request(
        method,
        path,
        headers=headers(),
        **({"json": {"pass_sequence": 5}} if method == "POST" else {}),
    )
    assert owner.status_code == 400, owner.text
    assert owner.json()["detail"]["code"] in {
        "invalid_batch_schedules_json",
        "invalid_batch_schedule_definition",
    }
    assert source.calls == []
    assert definitions.reads == 0
    assert durable_counts(ledger) == before


@pytest.mark.asyncio
async def test_direct_scheduler_admits_before_discovery_and_preserves_daemon(scheduler_boundary):
    _, ledger, config, source, definitions, scheduler = scheduler_boundary
    context = batch_scheduler_caller_context(config, pass_sequence=4)
    before = durable_counts(ledger)
    with pytest.raises(ValueError, match="^batch_scheduler_not_found$"):
        await scheduler.run_due_schedules(
            config=config, caller_context=context.model_copy(update={"tenant_id": "tenant-foreign"})
        )
    assert source.calls == []
    assert definitions.reads == 0
    assert durable_counts(ledger) == before
    process = BatchSchedulerProcess(
        scheduler=scheduler, config=BatchSchedulerProcessConfig(scheduler_config=config)
    )
    await process.run(max_iterations=1)
    assert durable_counts(ledger)[0] == before[0] + 1
    assert durable_counts(ledger)[1] == before[1] + 1
    assert source.calls == [("SCHEDULE_PORTFOLIO", config.tenant_id)]
