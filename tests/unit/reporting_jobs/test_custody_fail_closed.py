"""Retained-offer refusal and settlement controls; bindings are synthetic only."""

from datetime import UTC, datetime

import pytest

import app.reporting_jobs.custody_recovery as recovery
from app.reporting_jobs.custody_policy import CustodyRecoveryError, offer_digest
from app.reporting_jobs.ledger import ReportJobLedger
from tests.unit.reporting_jobs.test_retained_custody import (
    SyntheticBindings,
    metadata,
    ready,
    retain,
)


@pytest.fixture
def retained(tmp_path):
    ledger = ReportJobLedger(tmp_path / "custody.db", custody_bindings=SyntheticBindings())
    job, work, fence, payload = ready(ledger)
    retain(ledger, job, fence, payload)
    scope = {"job_id": job.job_id, "tenant_id": job.tenant_id, "region": job.region}
    return ledger, job, work, fence, payload, scope


def test_missing_job_and_required_work_refuse(retained):
    ledger, job, work, _, _, _ = retained
    with pytest.raises(CustodyRecoveryError, match="custody_job_not_found"):
        ledger.custody.observe("absent")
    with ledger._connect() as connection:
        connection.execute(
            "DELETE FROM report_job_work_item WHERE work_item_id=?", (work.work_item_id,)
        )
    with ledger._connect() as connection:
        with pytest.raises(CustodyRecoveryError, match="custody_work_missing"):
            ledger.custody.lock_pair(connection, job.job_id)


def test_stale_update_refuses_without_mutation(retained):
    ledger, job, _, _, _, _ = retained
    before = metadata(ledger, job.job_id)
    with ledger._connect() as connection:
        with pytest.raises(CustodyRecoveryError, match="custody_stale_write"):
            ledger.custody.execute(
                connection,
                "UPDATE report_job SET custody_epoch=99 WHERE report_job_id=?",
                ("absent",),
            )
    assert metadata(ledger, job.job_id) == before


@pytest.mark.parametrize("limit", ["MAX_RETAINED_BYTES", "MAX_TENANT_BYTES"])
def test_retention_quota_refuses_before_commit(tmp_path, monkeypatch, limit):
    ledger = ReportJobLedger(tmp_path / "quota.db", custody_bindings=SyntheticBindings())
    job, _, fence, payload = ready(ledger)
    monkeypatch.setattr(recovery, limit, 0)
    with pytest.raises(CustodyRecoveryError, match="custody_retention_quota_exceeded"):
        retain(ledger, job, fence, payload)
    assert metadata(ledger, job.job_id)["render_offer_sha256"] is None
    assert ledger.get_job(job.job_id).status == "data_ready"


@pytest.mark.parametrize(
    "fault,code",
    [
        ("missing_id", "render_identity_missing"),
        ("output", "offer_identity_mismatch"),
        ("scope", "offer_scope_mismatch"),
        ("status", "offer_not_ready"),
    ],
)
def test_offer_refusal_preserves_original_job(tmp_path, fault, code):
    ledger = ReportJobLedger(tmp_path / "offer.db", custody_bindings=SyntheticBindings())
    job, _, fence, payload = ready(ledger)
    if fault == "missing_id":
        payload["render_job_id"] = ""
    elif fault == "output":
        payload["output_format"] = "csv"
    elif fault == "scope":
        payload["render_context"]["archive"]["region"] = "other"
    else:
        with ledger._connect() as connection:
            connection.execute(
                "UPDATE report_job SET status='collecting_data' WHERE report_job_id=?",
                (job.job_id,),
            )
    before = metadata(ledger, job.job_id)
    with pytest.raises(CustodyRecoveryError, match=code):
        retain(ledger, job, fence, payload)
    assert metadata(ledger, job.job_id) == before


@pytest.mark.parametrize("fault", ["render", "request"])
def test_response_identity_conflict_parks_original_work(retained, fault):
    ledger, job, work, fence, _, scope = retained
    response = {
        "render_job_id": metadata(ledger, job.job_id)["render_job_id"],
        "archive_request_id": "archive-original",
    }
    ledger.custody.disposition(**scope, fence=fence, response=response, http_status=200)
    response["render_job_id" if fault == "render" else "archive_request_id"] = "other"
    ledger.custody.disposition(**scope, fence=fence, response=response, http_status=200)
    row = metadata(ledger, job.job_id)
    assert row["custody_state"] == "blocked"
    assert row["custody_reason"] == "custody_identity_conflict"
    assert row["archive_request_id"] == "archive-original"
    assert ledger.get_work_item_for_job(job.job_id).work_item_id == work.work_item_id
    assert ledger.get_work_item_for_job(job.job_id).status == "failed"


@pytest.mark.parametrize("status,state", [(403, None), (200, "archive_refused_policy")])
def test_owner_refusal_is_terminal_for_automatic_work(retained, status, state):
    ledger, job, _, fence, _, scope = retained
    ledger.custody.disposition(
        **scope, fence=fence, response={"archive_state": state}, http_status=status
    )
    assert metadata(ledger, job.job_id)["custody_state"] == "refused"
    assert ledger.get_work_item_for_job(job.job_id).status == "failed"


@pytest.mark.parametrize("fault", ["missing", "render", "request", "changed_after_verify"])
@pytest.mark.asyncio
async def test_final_verification_refuses_incomplete_or_changed_identity(
    retained, fault, monkeypatch
):
    ledger, job, _, fence, _, scope = retained
    response = {
        "render_job_id": metadata(ledger, job.job_id)["render_job_id"],
        "archive_request_id": "archive-original",
        "archive_document_id": "document",
        "artifact_sha256": "sha256:artifact",
    }
    ledger.custody.disposition(**scope, fence=fence, response=response, http_status=200)
    verified = []

    class Verify(SyntheticBindings):
        async def verify_final_custody(self, **kwargs):
            verified.append(kwargs["identity"])
            original_lock = ledger.custody.lock_pair

            def changed_read(*args):
                work, row = original_lock(*args)
                return work, {**dict(row), "archive_request_id": "raced"}

            monkeypatch.setattr(ledger.custody, "lock_pair", changed_read)

    ledger.custody.bindings = Verify()
    if fault == "missing":
        response.pop("artifact_sha256")
    elif fault in {"render", "request"}:
        response["render_job_id" if fault == "render" else "archive_request_id"] = "other"
    with pytest.raises(CustodyRecoveryError, match="custody_verification_identity"):
        await ledger.custody.confirm_final(**scope, fence=fence, response=response)
    assert len(verified) == (1 if fault == "changed_after_verify" else 0)
    assert ledger.get_job(job.job_id).status != "archived"
    assert metadata(ledger, job.job_id)["custody_state"] != "verified"


@pytest.mark.parametrize("settlement", ["complete", "defer", "fail"])
@pytest.mark.asyncio
async def test_independently_verified_work_settles_without_retry_or_lost_offer(
    retained, settlement
):
    ledger, job, work, fence, _, scope = retained
    body = ledger.custody.load_offer(**scope)
    response = {
        "render_job_id": metadata(ledger, job.job_id)["render_job_id"],
        "archive_request_id": "archive-original",
        "archive_document_id": "document",
        "artifact_sha256": "sha256:artifact",
    }
    ledger.custody.disposition(**scope, fence=fence, response=response, http_status=200)
    proofs = []

    class Verify(SyntheticBindings):
        async def verify_final_custody(self, **kwargs):
            proofs.append(kwargs["identity"])

    ledger.custody.bindings = Verify()
    await ledger.custody.confirm_final(**scope, fence=fence, response=response)
    args = {"work_item_id": work.work_item_id, "lease_token": work.lease_token}
    if settlement == "complete":
        result = ledger.complete_work_item(**args)
    elif settlement == "defer":
        result = ledger.defer_work_item(**args, wait_reason="late_wait", delay_seconds=1)
    else:
        result = ledger.fail_work_item(**args, error_category="late_failure", error_summary="late")
    assert result.status == "completed"
    assert ledger.get_job(job.job_id).status == "archived"
    assert ledger.custody.load_offer(**scope) == body
    assert proofs[0]["offer_sha256"] == offer_digest(body)
    assert proofs[0]["epoch"] == fence.epoch
    with ledger._connect() as connection:
        assert ledger.custody.guard_transition(connection, job.job_id, "archived") is False
        assert ledger.custody.guard_transition(connection, job.job_id, "failed") is True
        ledger.custody.park(connection, None, metadata(ledger, job.job_id), "late")
    assert metadata(ledger, job.job_id)["custody_state"] == "verified"


def test_unknown_operation_and_missing_due_refuse(retained):
    ledger, _, _, fence, _, scope = retained
    with pytest.raises(CustodyRecoveryError, match="custody_operation_invalid"):
        ledger.custody.reserve(**scope, fence=fence, operation="unrecognized")
    with pytest.raises(CustodyRecoveryError, match="custody_due_invalid"):
        ledger.custody.record_owner_due(**scope, fence=fence, due=None)


def test_blocking_and_invalid_resume_preserve_retained_bytes(retained):
    ledger, job, _, fence, _, scope = retained
    body = ledger.custody.load_offer(**scope)
    ledger.custody.block(**scope, fence=fence)
    with pytest.raises(CustodyRecoveryError, match="custody_resume_conflict"):
        ledger.custody.resume(
            **scope, expected_epoch=fence.epoch + 1, expected_digest=offer_digest(body)
        )
    assert ledger.custody.load_offer(**scope) == body
    assert metadata(ledger, job.job_id)["custody_reason"] == "custody_owner_blocked"


def test_database_clock_unavailable_refuses():
    from types import SimpleNamespace

    custody = recovery.CustodyRecovery(None, postgres=True, bindings=None)
    connection = SimpleNamespace(
        execute=lambda *args: SimpleNamespace(fetchone=lambda: {"instant": None})
    )
    with pytest.raises(CustodyRecoveryError, match="custody_database_clock_unavailable"):
        custody.clock(connection, datetime.now(UTC))


@pytest.mark.parametrize(
    "target", ["collecting_data", "data_ready", "rendering", "completed", "archiving"]
)
def test_required_transition_without_fenced_port_refuses(retained, target):
    ledger, job, _, _, _, _ = retained
    before = metadata(ledger, job.job_id)
    expected = (
        "worker_lease_required"
        if target in {"collecting_data", "data_ready"}
        else "fenced_transition_required"
    )
    with ledger._connect() as connection:
        with pytest.raises(CustodyRecoveryError, match=expected):
            ledger.custody.guard_transition(connection, job.job_id, target)
    assert metadata(ledger, job.job_id) == before


def test_missing_work_protection_and_parking_refuse(retained):
    ledger, job, _, _, _, _ = retained
    row = metadata(ledger, job.job_id)
    with ledger._connect() as connection:
        ledger.custody.validate_work(connection, "absent", "token", datetime.now(UTC))
        with pytest.raises(CustodyRecoveryError, match="custody_work_missing"):
            ledger.custody.protect_work(connection, "absent")
        with pytest.raises(CustodyRecoveryError, match="custody_work_missing"):
            ledger.custody.park(connection, None, row, "missing")


def test_required_claim_refuses_short_lease_and_rolls_back(tmp_path):
    from tests.unit.reporting_jobs.test_retained_custody import submit

    ledger = ReportJobLedger(tmp_path / "short.db", custody_bindings=SyntheticBindings())
    job = submit(ledger)
    with pytest.raises(CustodyRecoveryError, match="custody_lease_budget_invalid"):
        ledger.claim_work_items(worker_id="short", limit=1, lease_seconds=119)
    work = ledger.get_work_item_for_job(job.job_id)
    assert work.status == "pending" and work.attempt_count == 0
    assert work.lease_token is None


def test_resume_replay_converges_on_existing_work_without_new_epoch(retained):
    ledger, job, work, fence, _, scope = retained
    body = ledger.custody.load_offer(**scope)
    with ledger._connect() as connection:
        connection.execute(
            "UPDATE report_job SET custody_dispatch_count=3 WHERE report_job_id=?", (job.job_id,)
        )
    assert ledger.custody.reserve(**scope, fence=fence, operation="retry") is False
    blocked_epoch = metadata(ledger, job.job_id)["custody_epoch"]
    args = dict(**scope, expected_epoch=blocked_epoch, expected_digest=offer_digest(body))
    ledger.custody.resume(**args)
    first = metadata(ledger, job.job_id)
    ledger.custody.resume(**args)
    assert metadata(ledger, job.job_id) == first
    assert first["custody_epoch"] == blocked_epoch + 1
    assert ledger.get_work_item_for_job(job.job_id).work_item_id == work.work_item_id
