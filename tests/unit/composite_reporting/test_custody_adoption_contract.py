"""Committed custody needs every Report-owned field and the original artifact digest."""

import pytest

from app.reporting_jobs.ledger import InvalidReportJobTransitionError, ReportJobLedger
from app.reporting_lineage.store import ReportInputSnapshotNotFoundError, ReportInputSnapshotStore
from tests.unit.composite_reporting.test_custody_recovery import (
    RenderBoundary,
    Resolver,
    failed_source,
    recovery,
    replay,
)
from tests.unit.composite_reporting.test_custody_resolution_contract import custody_state

RETENTION = {"retention_policy_id": "bank-seven-year", "retain_until_date": "2033-01-31"}


def committed_custody(source, first):
    package = first.packages[0]
    return {
        **package["render_context"]["archive"],
        **{
            key: package[key]
            for key in (
                "snapshot_id",
                "render_job_id",
                "report_type",
                "template_id",
                "template_version",
                "report_data_contract_version",
            )
        },
        "report_job_id": source.job_id,
        "archive_request_id": source.archive_request_id,
        "document_id": "doc_original",
        "output_format": "xlsx",
        "checksum": "f" * 64,
        "declared_artifact_sha256": "f" * 64,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("corrected", [False, True])
@pytest.mark.parametrize(
    "field",
    [
        "classification",
        "frequency",
        "retention_start_date",
        "retention_policy_id",
        "retain_until_date",
        "portfolio_id",
    ],
)
@pytest.mark.parametrize("missing", [False, True])
async def test_incomplete_or_mismatched_custody_never_adopts(tmp_path, corrected, field, missing):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, snapshot, caller, first = await failed_source(
        ledger, store, corrected=corrected, request_options=RETENTION
    )
    payload = committed_custody(source, first)
    assert field in payload
    if missing:
        del payload[field]
    else:
        payload[field] = "foreign"
    boundary = RenderBoundary("archived_verified")
    before = custody_state(ledger, source)
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(recovery(ledger, store, Resolver(200, payload), boundary), source, caller)
    assert custody_state(ledger, source) == before
    assert store.get_snapshot_by_job(source.job_id) == snapshot
    assert not boundary.packages


@pytest.mark.asyncio
@pytest.mark.parametrize("digest", [None, ""])
@pytest.mark.parametrize("archive_digest", [None, "arbitrary"])
async def test_missing_original_digest_never_adopts(tmp_path, digest, archive_digest):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, _, caller, first = await failed_source(
        ledger, store, response_overrides={"artifact_sha256": digest}
    )
    assert source.render_artifact_sha256 is None
    payload = committed_custody(source, first)
    payload.update(checksum=archive_digest, declared_artifact_sha256=archive_digest)
    boundary = RenderBoundary("archived_verified")
    before = custody_state(ledger, source)
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(recovery(ledger, store, Resolver(200, payload), boundary), source, caller)
    assert custody_state(ledger, source) == before
    assert not boundary.packages


@pytest.mark.asyncio
@pytest.mark.parametrize("corrected", [False, True])
async def test_complete_retention_and_artifact_custody_is_adopted(tmp_path, corrected):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, snapshot, caller, first = await failed_source(
        ledger, store, corrected=corrected, request_options=RETENTION
    )
    boundary = RenderBoundary("archived_verified")
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(
            recovery(ledger, store, Resolver(200, committed_custody(source, first)), boundary),
            source,
            caller,
        )
    assert ledger.get_job(source.job_id).archive_document_id == "doc_original"
    assert store.get_snapshot_by_job(source.job_id) == snapshot
    assert not boundary.packages


@pytest.mark.asyncio
async def test_completed_composite_replay_still_requires_retained_source(tmp_path, monkeypatch):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, _, caller, _ = await failed_source(ledger, store)
    boundary = RenderBoundary("archived_verified")
    service = recovery(ledger, store, Resolver(), boundary)
    assert (await replay(service, source, caller)).replayed_job.status == "archived"
    before = custody_state(ledger, source)
    original = store.get_snapshot_by_job

    def purged(job_id):
        if job_id == source.job_id:
            raise ReportInputSnapshotNotFoundError("report_input_snapshot_not_found")
        return original(job_id)

    monkeypatch.setattr(store, "get_snapshot_by_job", purged)
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(service, source, caller)
    assert custody_state(ledger, source) == before
    assert len(boundary.packages) == 1
