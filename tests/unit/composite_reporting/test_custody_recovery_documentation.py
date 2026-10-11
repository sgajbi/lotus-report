"""Execute the operator guide's synthetic curl request through the registered route."""

import json
import re
import shlex
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from app.main import app
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render.replay_service import get_portfolio_review_replay_service
from tests.unit.composite_reporting.test_custody_adoption_contract import committed_custody
from tests.unit.composite_reporting.test_custody_recovery import (
    RenderBoundary,
    Resolver,
    failed_source,
    recovery,
)
from tests.unit.composite_reporting.test_custody_resolution_contract import custody_state

GUIDE = Path(__file__).resolve().parents[3] / "docs/operations/composite-custody-recovery.md"


def documented_request():
    example = re.search(r"```curl-config\n(.*?)\n```", GUIDE.read_text(), re.DOTALL)
    assert example, "Recovery guide must contain its executable synthetic curl config"
    options = {}
    headers = {}
    for line in example.group(1).splitlines():
        name, value = shlex.split(line.replace(" = ", " ", 1))
        if name == "header":
            key, content = value.split(": ", 1)
            headers[key] = content
        else:
            options[name] = value
    assert options["request"] == "POST"
    assert options["url"] == "http://report.dev.lotus/reports/jobs/rjob_synthetic_failed/replay"
    return urlsplit(options["url"]).path, headers, json.loads(options["data"])


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["replacement", "adoption", "refusal", "segregation"])
async def test_documented_replay_request_matches_registered_contract(
    tmp_path, monkeypatch, outcome
):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "true")
    path, headers, body = documented_request()
    ledger = ReportJobLedger(tmp_path / "jobs")
    store = ReportInputSnapshotStore(tmp_path / "snapshots")
    source, snapshot, caller, first = await failed_source(ledger, store)
    assert headers["X-Actor-Id"] == caller.triggered_by
    assert headers["X-Caller-Application"] == caller.caller_application
    assert headers["X-Tenant-Id"] == caller.tenant_id
    assert headers["X-Region"] == caller.region
    assert headers["Idempotency-Key"] == "custody-replay-synthetic-v1"
    assert headers["X-Role"] == "SUPPORT"
    assert headers["X-Service-Identity"] == "synthetic-trusted-gateway"
    assert body == {"reason": "Recover custody."}
    resolver = Resolver(
        200 if outcome == "adoption" else 503 if outcome == "refusal" else 404,
        committed_custody(source, first) if outcome == "adoption" else None,
    )
    boundary = RenderBoundary("archived_verified")
    service = recovery(ledger, store, resolver, boundary)
    monkeypatch.setitem(
        app.dependency_overrides, get_portfolio_review_replay_service, lambda: service
    )
    before = custody_state(ledger, source)
    if outcome == "segregation":
        headers["X-Tenant-Id"] = "foreign"
    # Only the synthetic opaque job identifier changes; headers/body are executed as authored.
    path = path.replace("rjob_synthetic_failed", source.job_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://report"
    ) as client:
        response = await client.post(path, headers=headers, json=body)
        if outcome == "replacement":
            assert response.status_code == 202, response.text
            child_id = response.json()["replayed_report_job_id"]
            assert response.json()["status"] == "archived"
            assert store.get_snapshot_by_job(child_id).snapshot_payload == snapshot.snapshot_payload
            repeated = await client.post(path, headers=headers, json=body)
            assert repeated.status_code == 202 and repeated.json() == response.json()
            assert len(boundary.packages) == 1
        else:
            assert response.status_code == (404 if outcome == "segregation" else 409)
            assert response.json()["detail"]["code"] == (
                "report_job_not_found"
                if outcome == "segregation"
                else "report_job_cannot_be_replayed"
            )
            assert not boundary.packages
            if outcome == "adoption":
                assert ledger.get_job(source.job_id).status == "archived"
            else:
                assert custody_state(ledger, source) == before
    assert store.get_snapshot_by_job(source.job_id) == snapshot
    assert bool(resolver.calls) is (outcome != "segregation")


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing_role", "empty_role", "missing_principal"])
async def test_documented_authz_denials_precede_recovery(tmp_path, monkeypatch, mutation):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "true")
    path, headers, body = documented_request()
    ledger = ReportJobLedger(tmp_path / "jobs")
    store = ReportInputSnapshotStore(tmp_path / "snapshots")
    source, snapshot, _, _ = await failed_source(ledger, store)
    boundary, resolver = RenderBoundary("archived_verified"), Resolver()
    service = recovery(ledger, store, resolver, boundary)
    monkeypatch.setitem(
        app.dependency_overrides, get_portfolio_review_replay_service, lambda: service
    )
    before = custody_state(ledger, source)
    if mutation == "missing_role":
        del headers["X-Role"]
    elif mutation == "empty_role":
        headers["X-Role"] = ""
    else:
        del headers["X-Service-Identity"]
        headers.pop("Authorization", None)
    path = path.replace("rjob_synthetic_failed", source.job_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://report"
    ) as client:
        response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 403, response.text
    assert response.json() == {
        "detail": "authorization_policy_denied",
        "reason": "missing_service_identity"
        if mutation == "missing_principal"
        else "missing_headers:x-role",
    }
    assert not resolver.calls and not boundary.packages
    assert custody_state(ledger, source) == before
    assert store.get_snapshot_by_job(source.job_id) == snapshot
