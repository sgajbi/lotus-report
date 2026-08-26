from __future__ import annotations

from uuid import uuid4

import pytest

from app.report_batch_orchestrator.ledger import ReportBatchLedger
from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
    PortfolioBatchCandidate,
)
from app.report_batch_orchestrator.tenant_admission import (
    BATCH_NOT_FOUND,
    admit_batch,
    load_admitted_batch,
)
from app.reporting_jobs.models import ReportCallerContext


def _caller(tenant_id: str) -> ReportCallerContext:
    suffix = uuid4().hex
    return ReportCallerContext.model_validate(
        {
            "triggered_by": "advisor-123",
            "caller_application": "lotus-gateway",
            "tenant_id": tenant_id,
            "region": "APAC",
            "booking_center_code": "SG",
            "role": "advisor",
            "correlation_id": f"corr-tenant-admission-{suffix}",
            "trace_id": f"trace-tenant-admission-{suffix}",
        }
    )


def _request(tenant_id: str) -> BatchCreateRequest:
    return BatchCreateRequest(
        selector_mode="explicit_portfolio_list",
        portfolio_ids=["PORT-1"],
        source_candidates=[
            PortfolioBatchCandidate(
                portfolio_id="PORT-1",
                tenant_id=tenant_id,
                region="APAC",
                active=True,
                selected=True,
            )
        ],
        as_of_date="2026-04-22",
        requested_output_formats=["json"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW"]},
    )


def _ledger_with_batch(tmp_path, tenant_id: str) -> tuple[ReportBatchLedger, str]:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    record = ledger.create_batch(
        request=_request(tenant_id),
        caller_context=_caller(tenant_id),
        idempotency_key=f"tenant-admission-{tenant_id}",
    )
    return ledger, record.batch_id


def test_admit_batch_returns_the_record_for_the_owning_tenant(tmp_path):
    ledger, batch_id = _ledger_with_batch(tmp_path, "tenant-sg")

    admitted = admit_batch(ledger.get_batch(batch_id), caller_context=_caller("tenant-sg"))

    assert admitted.batch_id == batch_id
    assert admitted.tenant_id == "tenant-sg"


def test_admit_batch_fails_closed_for_another_tenant(tmp_path):
    ledger, batch_id = _ledger_with_batch(tmp_path, "tenant-sg")

    with pytest.raises(ValueError) as excinfo:
        admit_batch(ledger.get_batch(batch_id), caller_context=_caller("tenant-uk"))

    assert str(excinfo.value) == BATCH_NOT_FOUND


def test_cross_tenant_and_unknown_identifiers_raise_the_same_signal(tmp_path):
    """The error contract must not disclose that another tenant owns the identifier."""

    ledger, batch_id = _ledger_with_batch(tmp_path, "tenant-sg")
    other_tenant = _caller("tenant-uk")

    with pytest.raises(ValueError) as cross_tenant:
        load_admitted_batch(ledger=ledger, batch_id=batch_id, caller_context=other_tenant)
    with pytest.raises(ValueError) as unknown:
        load_admitted_batch(
            ledger=ledger,
            batch_id="rbch_does_not_exist",
            caller_context=other_tenant,
        )

    assert str(cross_tenant.value) == str(unknown.value) == BATCH_NOT_FOUND


def test_load_admitted_batch_returns_the_record_for_the_owning_tenant(tmp_path):
    ledger, batch_id = _ledger_with_batch(tmp_path, "tenant-sg")

    admitted = load_admitted_batch(
        ledger=ledger,
        batch_id=batch_id,
        caller_context=_caller("tenant-sg"),
    )

    assert admitted.batch_id == batch_id


def test_admission_does_not_read_durable_state_beyond_the_batch_record(tmp_path):
    """Cross-tenant admission must stop before any further durable lookup."""

    ledger, batch_id = _ledger_with_batch(tmp_path, "tenant-sg")
    reads: list[str] = []

    class _RecordingLedger:
        def get_batch(self, batch_id_value: str):
            reads.append(batch_id_value)
            return ledger.get_batch(batch_id_value)

    with pytest.raises(ValueError):
        load_admitted_batch(
            ledger=_RecordingLedger(),
            batch_id=batch_id,
            caller_context=_caller("tenant-uk"),
        )

    assert reads == [batch_id]
