"""Ambiguity never authorizes replacement; only Archive's native absence contract does."""

import httpx
import pytest

from app.clients.http_resilience import response_payload
from app.reporting_jobs.ledger import InvalidReportJobTransitionError, ReportJobLedger
from app.reporting_jobs.models import ReportJobListFilters
from app.reporting_lineage.store import ReportInputSnapshotStore
from tests.unit.composite_reporting.test_custody_recovery import (
    NATIVE_ARCHIVE_ABSENCE,
    RenderBoundary,
    Resolver,
    failed_source,
    recovery,
    replay,
)

NATIVE_ABSENCE = NATIVE_ARCHIVE_ABSENCE


def custody_state(ledger, source):
    return (
        ledger.get_job(source.job_id),
        ledger.list_job_relationships(source.job_id),
        ledger.list_status_events(source.job_id),
        ledger.list_jobs(filters=ReportJobListFilters(tenant_id=source.tenant_id, limit=200)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [None, "unknown-custody", {}, 42, "archived_verified"])
async def test_unknown_handoff_retains_original_identity_and_resolves_first(tmp_path, state):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, snapshot, caller, first = await failed_source(
        ledger, store, state=state, response_overrides={"archive_document_id": None}
    )
    original_id = "areq_" + first.packages[0]["render_job_id"]
    assert source.archive_request_id == original_id
    boundary, resolver = RenderBoundary("archived_verified"), Resolver(503)
    before = custody_state(ledger, source)
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(recovery(ledger, store, resolver, boundary), source, caller)
    assert resolver.calls[0][0] == original_id
    assert custody_state(ledger, source) == before
    assert not boundary.packages
    assert store.get_snapshot_by_job(source.job_id) == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [None, "", "  ", {}, 42])
async def test_unknown_handoff_without_identity_never_guesses_from_artifact(tmp_path, identity):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, _, caller, _ = await failed_source(
        ledger, store, state=None, response_overrides={"archive_request_id": identity}
    )
    assert source.archive_request_id is None
    assert not source.retry_eligible
    boundary, resolver = RenderBoundary("archived_verified"), Resolver(404, NATIVE_ABSENCE)
    before = custody_state(ledger, source)
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(recovery(ledger, store, resolver, boundary), source, caller)
    assert custody_state(ledger, source) == before
    assert not resolver.calls and not boundary.packages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, text="<html>proxy route not found</html>"),
        httpx.Response(404, content=b""),
        httpx.Response(404, json={}),
        httpx.Response(404, json={"error": {"code": "route_not_found"}}),
        httpx.Response(404, json={"error": {"code": "document_binary_missing"}}),
        httpx.Response(404, json={"error": "document_not_found"}),
        httpx.Response(404, json={"error": {"code": ["document_not_found"]}}),
        httpx.Response(404, json=[{"error": {"code": "document_not_found"}}]),
    ],
)
async def test_false_absence_has_zero_replacement_side_effects(tmp_path, response):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, snapshot, caller, _ = await failed_source(ledger, store)
    resolver, boundary = (
        Resolver(404, response_payload(response)),
        RenderBoundary("archived_verified"),
    )
    before = custody_state(ledger, source)
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(recovery(ledger, store, resolver, boundary), source, caller)
    assert custody_state(ledger, source) == before
    assert not boundary.packages
    assert store.get_snapshot_by_job(source.job_id) == snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception", [httpx.ReadTimeout("lost response"), RuntimeError("resolver down")]
)
async def test_resolver_timeout_or_exception_has_zero_replacement_side_effects(tmp_path, exception):
    class RaisingResolver:
        async def get_document_by_request_id(self, request_id, **context):
            raise exception

    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, _, caller, _ = await failed_source(ledger, store)
    boundary = RenderBoundary("archived_verified")
    before = custody_state(ledger, source)
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(recovery(ledger, store, RaisingResolver(), boundary), source, caller)
    assert custody_state(ledger, source) == before
    assert not boundary.packages


@pytest.mark.asyncio
@pytest.mark.parametrize("corrected", [False, True])
async def test_native_absence_after_unknown_handoff_preserves_frozen_facts(tmp_path, corrected):
    ledger, store = ReportJobLedger(tmp_path / "jobs"), ReportInputSnapshotStore(tmp_path / "snap")
    source, snapshot, caller, _ = await failed_source(
        ledger, store, state=None, corrected=corrected
    )
    boundary, resolver = RenderBoundary("archived_verified"), Resolver(404, NATIVE_ABSENCE)
    service = recovery(ledger, store, resolver, boundary)
    child = (await replay(service, source, caller)).replayed_job
    assert resolver.calls[0][0] == source.archive_request_id
    assert child.status == "archived"
    assert child.accepted_document_contract == source.accepted_document_contract
    assert store.get_snapshot_by_job(child.job_id).snapshot_payload == snapshot.snapshot_payload
    assert child.render_artifact_sha256 == source.render_artifact_sha256
    assert (await replay(service, source, caller)).replayed_job.job_id == child.job_id
    assert len(boundary.packages) == 1
