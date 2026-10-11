"""Owned PostgreSQL/API recovery; controlled downstream ports, no real workbook claim."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.main import app
from app.reporting_jobs.ledger import InvalidReportJobTransitionError
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger
from app.reporting_jobs.replay_policy import composite_replay_request
from app.reporting_lineage.postgres_store import PostgresReportInputSnapshotStore
from app.reporting_render.replay_service import (
    get_portfolio_review_replay_service,
    replay_idempotency_key,
)
from tests.integration.postgres_adapter_ownership import own_postgres_adapter
from tests.integration.test_composite_twr_invocation_authority import read_from_fresh_process
from tests.unit.composite_reporting.test_custody_recovery import (
    RenderBoundary,
    Resolver,
    failed_source,
    recovery,
    replay,
)
from tests.unit.composite_reporting.test_custody_resolution_contract import custody_state


def adapters():
    url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not url:
        pytest.skip("Owned PostgreSQL required")
    return (
        own_postgres_adapter(PostgresReportJobLedger(url)),
        own_postgres_adapter(PostgresReportInputSnapshotStore(url)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["archive_pending", None])
async def test_postgres_registered_replay_foreign_refusal_and_restart(monkeypatch, state):
    ledger, store = adapters()
    source, snapshot, caller, _ = await failed_source(ledger, store, corrected=True, state=state)
    ledger.close()
    store.close()
    ledger, store = adapters()
    boundary, resolver = RenderBoundary("archived_verified"), Resolver()
    service = recovery(ledger, store, resolver, boundary)
    monkeypatch.setitem(
        app.dependency_overrides, get_portfolio_review_replay_service, lambda: service
    )
    headers = {
        "X-Actor-Id": caller.triggered_by,
        "X-Caller-Application": caller.caller_application,
        "X-Tenant-Id": caller.tenant_id,
        "X-Region": caller.region,
        "X-Correlation-ID": caller.correlation_id,
        "X-Trace-ID": caller.trace_id,
        "Idempotency-Key": "recover-pg",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://report"
    ) as client:
        url = f"/reports/jobs/{source.job_id}/replay"
        foreign = await client.post(
            url, headers={**headers, "X-Tenant-Id": "foreign"}, json={"reason": "Recover custody."}
        )
        assert foreign.status_code == 404 and not resolver.calls
        first = await client.post(url, headers=headers, json={"reason": "Recover custody."})
        assert first.status_code == 202, first.text
        second = await client.post(url, headers=headers, json={"reason": "Recover custody."})
        assert second.status_code == 202 and second.json() == first.json()
    child = (await replay(service, source, caller, "recover-pg")).replayed_job
    assert child.status == "archived" and len(boundary.packages) == 1
    assert store.get_snapshot_by_job(child.job_id).snapshot_payload == snapshot.snapshot_payload
    ledger.close()
    store.close()
    ledger, store = adapters()
    assert ledger.get_job(child.job_id) == child
    assert store.get_snapshot_by_job(child.job_id).report_revision_id == snapshot.report_revision_id
    read_from_fresh_process(child.job_id, child, store.get_snapshot_by_job(child.job_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("child_state", ["archive_failed", None])
async def test_postgres_concurrent_novel_keys_create_only_one_replacement(child_state):
    ledger, store = adapters()
    source, _, caller, _ = await failed_source(ledger, store, state=None)
    boundary, resolver = RenderBoundary(child_state, "archive_unreachable: lost"), Resolver()
    service = recovery(ledger, store, resolver, boundary)

    def attempt(key):
        try:
            return asyncio.run(replay(service, source, caller, key)).replayed_job
        except InvalidReportJobTransitionError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["concurrent-a", "concurrent-b"]))
    assert sum(result is not None for result in results) == 1
    assert len(boundary.packages) == 1
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(service, source, caller, "after-ambiguous-child")


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [False, True])
async def test_postgres_original_custody_adoption_is_exact_and_durable(foreign):
    ledger, store = adapters()
    source, snapshot, caller, first = await failed_source(ledger, store, state=None)
    package = first.packages[0]
    payload = {
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
        "document_id": "doc_committed_before_response_loss",
        "output_format": "xlsx",
        "checksum": "f" * 64,
        "declared_artifact_sha256": "f" * 64,
    }
    if foreign:
        payload["tenant_id"] = "foreign"
    boundary = RenderBoundary("archived_verified")
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(recovery(ledger, store, Resolver(200, payload), boundary), source, caller)
    job = ledger.get_job(source.job_id)
    assert job.status == ("failed" if foreign else "archived")
    assert not boundary.packages
    read_from_fresh_process(source.job_id, job, snapshot)


@pytest.mark.asyncio
async def test_postgres_concurrent_same_key_derivation_converges_then_renders_once():
    ledger, store = adapters()
    source, snapshot, caller, _ = await failed_source(ledger, store, state=None)
    key = "same-concurrent-key"

    def derive(_):
        return ledger.create_replay_derived_job(
            source_job_id=source.job_id,
            request=composite_replay_request(source),
            caller_context=caller,
            idempotency_key=replay_idempotency_key(
                source_job_id=source.job_id, idempotency_key=key
            ),
            reason="Recover custody.",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        derived = list(pool.map(derive, [0, 1]))
    assert derived[0].job_id == derived[1].job_id
    boundary = RenderBoundary("archived_verified")
    service = recovery(ledger, store, Resolver(), boundary)
    child = (await replay(service, source, caller, key)).replayed_job
    assert child.job_id == derived[0].job_id and child.status == "archived"
    assert (await replay(service, source, caller, key)).replayed_job == child
    assert len(boundary.packages) == 1
    assert store.get_snapshot_by_job(child.job_id).snapshot_payload == snapshot.snapshot_payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload", [{}, {"detail": "<html>proxy404</html>"}, {"error": {"code": "route_not_found"}}]
)
async def test_postgres_false_absence_has_no_durable_replacement_or_source_effects(payload):
    ledger, store = adapters()
    source, snapshot, caller, _ = await failed_source(ledger, store, state=None)
    before = custody_state(ledger, source)
    boundary = RenderBoundary("archived_verified")
    with pytest.raises(InvalidReportJobTransitionError):
        await replay(recovery(ledger, store, Resolver(404, payload), boundary), source, caller)
    assert custody_state(ledger, source) == before
    assert not boundary.packages
    read_from_fresh_process(source.job_id, source, snapshot)
