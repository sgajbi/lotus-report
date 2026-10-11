"""Malformed artifact identity and legacy authority refuse before custody mutation."""

import json

import httpx
import pytest

from app.composite_reporting.invocation_authority import BINDING_OPTION
from app.main import app
from app.reporting_jobs.ledger import InvalidReportJobTransitionError, ReportJobLedger
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render.replay_service import get_portfolio_review_replay_service
from tests.unit.composite_reporting.test_custody_adoption_contract import committed_custody
from tests.unit.composite_reporting.test_custody_recovery import (
    RenderBoundary,
    Resolver,
    failed_source,
    recovery,
    replay,
)
from tests.unit.composite_reporting.test_custody_resolution_contract import custody_state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "digest",
    [
        "sha256:",
        "arbitrary",
        "f" * 63,
        "f" * 65,
        "g" * 64,
        "sha256:sha256:" + "f" * 64,
        "sha512:" + "f" * 64,
    ],
)
async def test_malformed_original_digest_never_adopts_even_when_archive_echoes_it(tmp_path, digest):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, snapshot, caller, first = await failed_source(
        ledger, store, response_overrides={"artifact_sha256": digest}
    )
    payload = committed_custody(source, first)
    payload.update(
        checksum=digest.removeprefix("sha256:"),
        declared_artifact_sha256=digest.removeprefix("sha256:"),
    )
    boundary = RenderBoundary("archived_verified")
    before = custody_state(ledger, source)
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(recovery(ledger, store, Resolver(200, payload), boundary), source, caller)
    assert custody_state(ledger, source) == before
    assert store.get_snapshot_by_job(source.job_id) == snapshot
    assert not boundary.packages


@pytest.mark.asyncio
@pytest.mark.parametrize("digest", ["f" * 64, "sha256:" + "f" * 64, "F" * 64])
async def test_supported_original_digest_spellings_adopt_canonical_checksum(tmp_path, digest):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, _, caller, first = await failed_source(
        ledger, store, response_overrides={"artifact_sha256": digest}
    )
    boundary = RenderBoundary("archived_verified")
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(
            recovery(ledger, store, Resolver(200, committed_custody(source, first)), boundary),
            source,
            caller,
        )
    assert ledger.get_job(source.job_id).archive_document_id == "doc_original"
    assert not boundary.packages


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing", "malformed", "job", "tenant"])
@pytest.mark.parametrize("status", [404, 200])
async def test_legacy_invalid_inherited_authority_is_registered_409(
    tmp_path, monkeypatch, mutation, status
):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, snapshot, caller, first = await failed_source(ledger, store)
    options = json.loads(json.dumps(source.options))
    if mutation == "missing":
        del options[BINDING_OPTION]
    elif mutation == "malformed":
        options[BINDING_OPTION] = {}
    elif mutation == "job":
        options[BINDING_OPTION]["job_id"] = "foreign"
    else:
        options[BINDING_OPTION]["admission"]["tenant_id"] = "foreign"
    # Simulate a persisted pre-deployment/invalid source, rather than bypassing
    # production job loading or mocking the authority refusal itself.
    with ledger._connect() as connection:
        connection.execute(
            "UPDATE report_request SET options_json = ? WHERE report_request_id = ?",
            (json.dumps(options), source.request_id),
        )
    source = ledger.get_job(source.job_id)
    resolver = Resolver(status, committed_custody(source, first) if status == 200 else None)
    boundary = RenderBoundary("archived_verified")
    service = recovery(ledger, store, resolver, boundary)
    monkeypatch.setitem(
        app.dependency_overrides, get_portfolio_review_replay_service, lambda: service
    )
    before = custody_state(ledger, source)
    headers = {
        "X-Actor-Id": caller.triggered_by,
        "X-Caller-Application": caller.caller_application,
        "X-Tenant-Id": caller.tenant_id,
        "X-Region": caller.region,
        "X-Correlation-ID": caller.correlation_id,
        "X-Trace-ID": caller.trace_id,
        "Idempotency-Key": "invalid-provenance",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://report"
    ) as client:
        response = await client.post(
            f"/reports/jobs/{source.job_id}/replay",
            headers=headers,
            json={"reason": "Recover custody."},
        )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "report_job_cannot_be_replayed"
    assert custody_state(ledger, source) == before
    assert store.get_snapshot_by_job(source.job_id) == snapshot
    assert not boundary.packages
