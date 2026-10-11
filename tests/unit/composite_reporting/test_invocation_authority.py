"""Report contract-flow proof; no native signature, IdP or grant-verifier claim."""

from dataclasses import replace
from datetime import datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.clients.performance_client import PerformanceClient
from app.composite_reporting.invocation_authority import (
    BINDING_OPTION,
    AuthenticatedTwrInitiator,
    TwrAttemptContext,
    TwrAuthorityRefused,
    UnavailableTwrInvocationProvider,
    authorize_selection,
    binding_for_job,
    configure_composite_twr_invocation_provider,
    get_composite_twr_invocation_provider,
    selector_set_for,
)
from app.composite_reporting.models import CompositeReviewJobRequest
from app.composite_reporting.router import get_authenticated_twr_initiator
from app.composite_reporting.source_capture import CompositeInputProvider
from app.main import app
from app.reporting_identity.capture_binding import series_key_for_job
from app.reporting_jobs.execution import ReportJobExecutionService
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext
from app.reporting_jobs.work_queue import ReportJobWorkRetryPolicy
from app.reporting_jobs.worker import ReportJobWorker
from app.reporting_lineage.capture_service import (
    PortfolioReviewInputCaptureError,
    PortfolioReviewSnapshotCaptureService,
    _stored_capture_failure,
)
from app.reporting_lineage.store import ReportInputSnapshotNotFoundError, ReportInputSnapshotStore
from tests.unit.composite_reporting.authority_contract_flow import (
    ContractFlowAuthority,
    contract_admission,
)
from tests.unit.composite_reporting.fixtures import calculated_example, selection_for
from tests.unit.composite_reporting.test_registered_lifecycle import (
    HEADERS,
    NoDocumentRender,
    composite_lifecycle,
)
from tests.unit.composite_reporting.test_source_capture import job_for


def request_and_caller():
    request = CompositeReviewJobRequest(selection=selection_for(calculated_example()))
    caller = ReportCallerContext(
        triggered_by="audit-diagnostic-only",
        caller_application="untrusted-diagnostic-only",
        tenant_id="tenant-a",
        region="APAC",
        role="SUPPORT",
        correlation_id="corr-authority-contract-flow",
        trace_id="trace-authority-contract-flow",
    )
    return request, caller


def exercise_durable_binding(ledger):
    request, caller = request_and_caller()
    admission = contract_admission(request)
    with pytest.raises(TwrAuthorityRefused, match="ADMISSION_REQUIRED"):
        ledger.submit_composite_review_job(
            request=request, caller_context=caller, idempotency_key="missing-binding"
        )
    job = ledger.submit_composite_review_job(
        request=request, caller_context=caller, idempotency_key="bound", twr_admission=admission
    )
    binding = binding_for_job(job)
    assert binding.admission == admission
    assert binding.job_id == job.job_id
    assert binding.request_id == job.request_id
    assert binding.correlation_id == caller.correlation_id
    assert ledger.get_work_item_for_job(job.job_id).status == "pending"
    later = admission.model_copy(update={"admitted_at": admission.admitted_at + timedelta(days=1)})
    same = ledger.submit_composite_review_job(
        request=request, caller_context=caller, idempotency_key="bound", twr_admission=later
    )
    assert same.job_id == job.job_id
    assert binding_for_job(same) == binding
    for field, value in (
        ("subject", "different-person"),
        ("principal_kind", "service"),
        ("effective_scope_digest", "c" * 64),
        ("trust_config_digest", "c" * 64),
        ("resolver_policy_version", "changed-policy"),
        ("binding_reference", "different-binding"),
    ):
        with pytest.raises(TwrAuthorityRefused):
            ledger.submit_composite_review_job(
                request=request,
                caller_context=caller,
                idempotency_key="bound",
                twr_admission=admission.model_copy(update={field: value}),
            )
    return job


def test_sqlite_atomic_binding_idempotency_and_reopen(tmp_path):
    path = tmp_path / "authority.sqlite3"
    ledger = ReportJobLedger(path)
    job = exercise_durable_binding(ledger)
    reopened = ReportJobLedger(path).get_job(job.job_id)
    assert binding_for_job(reopened) == binding_for_job(job)
    assert "synthetic-contract-flow-credential" not in reopened.model_dump_json()


def test_reserved_binding_is_rejected_and_only_security_metadata_leaves_series():
    request, _ = request_and_caller()
    with pytest.raises(ValidationError):
        CompositeReviewJobRequest(selection=request.selection, options={BINDING_OPTION: {}})
    job = job_for(calculated_example())
    without = job.model_copy(update={"options": request.capture_options()})
    assert series_key_for_job(job) == series_key_for_job(without)
    changed = job.model_copy(
        update={"options": {**job.options, "other_semantic_option": "changed"}}
    )
    assert series_key_for_job(changed) != series_key_for_job(job)


def test_portfolio_replay_cannot_copy_parent_twr_binding(tmp_path):
    ledger = ReportJobLedger(tmp_path / "replay.sqlite3")
    _, caller = request_and_caller()
    request = PortfolioReviewJobRequest(
        portfolio_scope={"portfolio_ids": ["portfolio-a"]},
        as_of_date="2026-01-31",
        options={BINDING_OPTION: {}},
    )
    with pytest.raises(TwrAuthorityRefused, match="CALLER_BINDING_FORBIDDEN"):
        ledger.submit_portfolio_review_job(
            request=request, caller_context=caller, idempotency_key="copied"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, "", "   "])
@pytest.mark.parametrize("available", [False, True])
async def test_missing_idempotency_refuses_before_authority_admission(
    tmp_path, monkeypatch, key, available
):
    with composite_lifecycle(tmp_path, monkeypatch) as (ledger, _, _, supplier):
        delegate = ContractFlowAuthority() if available else UnavailableTwrInvocationProvider()
        admissions = []

        class Authority:
            def admit(self, *args):
                admissions.append(args)
                return delegate.admit(*args)

        app.dependency_overrides[get_composite_twr_invocation_provider] = Authority
        headers = {name: value for name, value in HEADERS.items() if name != "Idempotency-Key"}
        if key is not None:
            headers["Idempotency-Key"] = key
        request, _ = request_and_caller()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            response = await client.post(
                "/reports/composite-reviews", json=request.model_dump(mode="json"), headers=headers
            )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "missing_idempotency_key"
        assert admissions == []
        assert supplier["calls"] == []
        with ledger._connect() as connection:
            assert connection.execute("SELECT count(*) FROM report_request").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted_context", [False, True])
async def test_public_headers_cannot_mint_authority_and_unavailable_default_refuses(
    tmp_path, monkeypatch, trusted_context
):
    from app.composite_reporting.invocation_authority import get_composite_twr_invocation_provider

    with composite_lifecycle(tmp_path, monkeypatch) as (ledger, _, _, supplier):
        app.dependency_overrides[get_composite_twr_invocation_provider] = lambda: (
            UnavailableTwrInvocationProvider()
        )
        if not trusted_context:
            app.dependency_overrides.pop(get_authenticated_twr_initiator)
        request, _ = request_and_caller()
        headers = {**HEADERS, "Authorization": "Bearer arbitrary-inbound", "X-Role": "SUPPORT"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            response = await client.post(
                "/reports/composite-reviews", json=request.model_dump(mode="json"), headers=headers
            )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "composite_twr_authority_unavailable"
        assert supplier["calls"] == []
        with ledger._connect() as connection:
            assert connection.execute("SELECT count(*) FROM report_request").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    ["missing", "malformed", "job", "tenant", "scope", "policy", "trust", "selector", "revoked"],
)
async def test_changed_or_legacy_authority_refuses_before_protected_io(defect):
    job = job_for(calculated_example())
    authority = ContractFlowAuthority()
    if defect == "missing":
        job.options.pop(BINDING_OPTION)
    elif defect == "malformed":
        job.options[BINDING_OPTION] = {"token": "must-not-escape"}
    elif defect == "job":
        job = job.model_copy(update={"job_id": "copied-job"})
    elif defect == "tenant":
        authority.current_updates = {"tenant_id": "other-tenant"}
    elif defect == "revoked":
        authority.refused = True
    else:
        field = {
            "scope": "effective_scope_digest",
            "policy": "resolver_policy_version",
            "trust": "trust_config_digest",
            "selector": "selector_set_digest",
        }[defect]
        authority.current_updates = {field: "c" * 64}

    class NoSource:
        async def get_composite_twr(self, *args, **kwargs):
            pytest.fail("Protected I/O must be zero")

    with pytest.raises(PortfolioReviewInputCaptureError) as caught:
        await CompositeInputProvider(
            performance_client=NoSource(), invocation_provider=authority
        ).collect_for_job(job)
    assert caught.value.upstream_calls == []
    assert "must-not-escape" not in str(caught.value.original_error)
    assert "untrusted-provider" not in str(caught.value.original_error)


@pytest.mark.asyncio
async def test_restart_rotation_current_refusal_and_nonsecret_attempt_evidence(
    tmp_path, monkeypatch
):
    ledger = ReportJobLedger(tmp_path / "rotation.sqlite3")
    job = exercise_durable_binding(ledger)
    authority = ContractFlowAuthority()
    sent = []

    async def transport(**kwargs):
        sent.append(kwargs)
        return 200, calculated_example()

    monkeypatch.setattr("app.clients.performance_client.post_with_retry", transport)
    client = PerformanceClient("http://performance", 1, max_retries=4, read_bearer_token="GET-only")
    for _ in range(2):
        reopened = ReportJobLedger(ledger._db_path).get_job(job.job_id)
        await CompositeInputProvider(
            performance_client=client, invocation_provider=authority, job_ledger=ledger
        ).collect_for_job(reopened)
    assert len(sent) == 2
    assert sent[0]["headers"]["Authorization"] != sent[1]["headers"]["Authorization"]
    assert all(item["max_retries"] == 0 for item in sent)
    events = ledger.list_status_events(job.job_id)
    attempts = [
        event for event in events if event.event_type == "composite_twr_invocation_authorized"
    ]
    assert len(attempts) == 2
    evidence = " ".join(event.model_dump_json() for event in events)
    assert "synthetic-contract-flow-credential" not in evidence
    assert "GET-only" not in evidence
    authority.refused = True
    with pytest.raises(PortfolioReviewInputCaptureError):
        await CompositeInputProvider(
            performance_client=client, invocation_provider=authority, job_ledger=ledger
        ).collect_for_job(reopened)
    assert len(sent) == 2


def test_delegated_shape_and_default_provider_are_fail_closed():
    request, _ = request_and_caller()
    data = contract_admission(request).model_dump()
    data.pop("delegated_actor")
    with pytest.raises(ValidationError):
        type(contract_admission(request)).model_validate(data)
    with pytest.raises(TwrAuthorityRefused):
        UnavailableTwrInvocationProvider().admit(
            AuthenticatedTwrInitiator(context_reference="trusted-test-context"),
            "tenant-a",
            selector_set_for(request),
        )


def test_trusted_startup_registration_stores_only_provider_composition():
    previous = get_composite_twr_invocation_provider()
    authority = ContractFlowAuthority()
    try:
        configure_composite_twr_invocation_provider(authority)
        assert get_composite_twr_invocation_provider() is authority
        assert authority.attempts == []
    finally:
        configure_composite_twr_invocation_provider(previous)


@pytest.mark.asyncio
async def test_actual_http_twr_single_send_and_dedicated_credential_isolation(monkeypatch):
    job = job_for(calculated_example())
    request, _ = request_and_caller()
    authority = ContractFlowAuthority()
    context = TwrAttemptContext(job.job_id, job.request_id, job.correlation_id, job.trace_id, "one")
    invocation = authorize_selection(job, request.selection, authority, context)
    sent = []

    def receiver(request):
        sent.append(request)
        return httpx.Response(503, json={"detail": "source-unavailable"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(receiver), **kwargs),
    )
    client = PerformanceClient("http://performance", 1, max_retries=4, read_bearer_token="GET-only")
    payload = request.selection.performance_request()
    status, _ = await client.get_composite_twr(
        payload, admitted_tenant_id=job.tenant_id, invocation=invocation
    )
    assert status == 503
    assert len(sent) == 1
    assert sent[0].url.path == "/performance/composites/twr"
    assert sent[0].headers["Authorization"] == "Bearer synthetic-contract-flow-credential-1"
    assert sent[0].headers["X-Tenant-Id"] == job.tenant_id
    assert (
        not {"X-Actor-Id", "X-Role", "X-Capabilities", "X-Service-Identity"}
        & sent[0].headers.keys()
    )
    assert "synthetic-contract-flow-credential" not in repr(invocation)
    for arguments in (
        {"payload": payload, "admitted_tenant_id": "foreign", "invocation": invocation},
        {
            "payload": {"different": True},
            "admitted_tenant_id": job.tenant_id,
            "invocation": invocation,
        },
        {"payload": payload, "admitted_tenant_id": job.tenant_id},
    ):
        with pytest.raises(TwrAuthorityRefused):
            await client.get_composite_twr(**arguments)
    assert len(sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_receiver_authority_denial_is_retained_and_definitive(tmp_path, monkeypatch, status):
    with composite_lifecycle(tmp_path, monkeypatch) as stack:
        ledger, store, worker, _ = stack
        sent = []

        async def transport(**kwargs):
            sent.append(kwargs)
            return status, {"detail": "untrusted-denial-text-must-not-escape"}

        monkeypatch.setattr("app.clients.performance_client.post_with_retry", transport)
        request, _ = request_and_caller()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            response = await client.post(
                "/reports/composite-reviews", json=request.model_dump(mode="json"), headers=HEADERS
            )
        assert response.status_code == 202
        job_id = response.json()["report_job_id"]
        result = await worker.run_once(worker_id="receiver-denial", max_items=1, lease_seconds=30)
        assert result.retry_pending_count == 0
        job = ledger.get_job(job_id)
        assert job.status == "failed"
        assert job.failure_category == "validation_failed"
        assert job.retry_eligible is False
        snapshot = store.get_snapshot_by_job(job_id)
        assert _stored_capture_failure(snapshot.snapshot_payload) == (
            "validation_failed",
            "Composite TWR invocation authority was refused.",
            False,
        )
        calls = store.list_upstream_calls(snapshot.snapshot_id)
        assert len(calls) == 1
        assert calls[0].status_code == status
        assert calls[0].supportability_status == "error"
        assert calls[0].completeness_status == "error"
        assert calls[0].response_hash is not None
        reopened_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
        reopened_store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")
        restored = await PortfolioReviewSnapshotCaptureService(
            snapshot_store=reopened_store, job_ledger=reopened_ledger
        ).capture_for_job(reopened_ledger.get_job(job_id))
        assert restored == job
        assert reopened_store.get_snapshot_by_job(job_id) == snapshot
        again = await worker.run_once(worker_id="no-retry", max_items=1, lease_seconds=30)
        assert again.claimed_count == 0
        assert len(sent) == 1
        assert "must-not-escape" not in job.model_dump_json() + snapshot.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("member", "value"),
    [
        ("admission", {}),
        ("admission", None),
        ("credential", "secret-must-not-escape"),
        ("credential", None),
        ("validated_at", "invalid-time"),
        ("validated_at", datetime(2026, 10, 11)),
        ("credential_id", 123),
        ("credential_id", ""),
        ("exact_selector_digest", None),
        ("exact_selector_digest", ""),
        ("request_payload_digest", None),
        ("request_payload_digest", ""),
    ],
)
async def test_malformed_invocation_members_are_terminal_before_io(
    tmp_path, monkeypatch, member, value
):
    with composite_lifecycle(tmp_path, monkeypatch) as (ledger, store, worker, supplier):
        from app.composite_reporting.source_capture import get_composite_twr_invocation_provider

        authority = get_composite_twr_invocation_provider()
        authorize = authority.authorize_attempt
        monkeypatch.setattr(
            authority,
            "authorize_attempt",
            lambda *args: replace(authorize(*args), **{member: value}),
        )
        request, _ = request_and_caller()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            response = await client.post(
                "/reports/composite-reviews", json=request.model_dump(mode="json"), headers=HEADERS
            )
        assert response.status_code == 202
        job_id = response.json()["report_job_id"]
        result = await worker.run_once(
            worker_id="malformed-authority", max_items=1, lease_seconds=30
        )
        assert result.retry_pending_count == 0
        job = ledger.get_job(job_id)
        assert job.status == "failed"
        assert job.failure_category == "validation_failed"
        assert job.retry_eligible is False
        snapshot = store.get_snapshot_by_job(job_id)
        assert _stored_capture_failure(snapshot.snapshot_payload) == (
            "validation_failed",
            "Composite TWR invocation authority was refused.",
            False,
        )
        assert snapshot.supportability_status == "error"
        assert snapshot.completeness_status == "error"
        assert store.list_upstream_calls(snapshot.snapshot_id) == []
        assert supplier["calls"] == []
        events = ledger.list_status_events(job_id)
        assert not any(
            event.event_type == "composite_twr_invocation_authorized" for event in events
        )
        retained = job.model_dump_json() + snapshot.model_dump_json()
        retained += " ".join(event.model_dump_json() for event in events)
        assert "secret-must-not-escape" not in retained
        again = await worker.run_once(worker_id="terminal-authority", max_items=1, lease_seconds=30)
        assert again.claimed_count == 0


async def exercise_worker_retry(
    tmp_path, monkeypatch, outcome, *, status=503, adapters=None, reopen_hook=None
):
    """Real worker/ledger flow with synthetic authority and controlled transport."""
    with composite_lifecycle(tmp_path, monkeypatch, adapters=adapters) as stack:
        ledger, store, worker, supplier = stack
        from app.composite_reporting.source_capture import get_composite_twr_invocation_provider

        authority = get_composite_twr_invocation_provider()
        sent = []

        async def transport(**kwargs):
            sent.append(kwargs)
            if len(sent) == 1 or outcome == "exhausted":
                if status == "transport":
                    raise httpx.ReadTimeout("secret-shaped-provider-text-must-not-escape")
                return status, {"detail": "untrusted-source-body-must-not-escape"}
            return 200, calculated_example()

        monkeypatch.setattr("app.clients.performance_client.post_with_retry", transport)
        policy = ReportJobWorkRetryPolicy(max_attempts=2, base_delay_seconds=0)
        worker._retry_policy = policy
        request, _ = request_and_caller()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            response = await client.post(
                "/reports/composite-reviews", json=request.model_dump(mode="json"), headers=HEADERS
            )
        assert response.status_code == 202, response.text
        job_id = response.json()["report_job_id"]
        first = await worker.run_once(worker_id="first", max_items=1, lease_seconds=30)
        assert first.retry_pending_count == 1
        assert ledger.get_job(job_id).status == "collecting_data"
        with pytest.raises(ReportInputSnapshotNotFoundError):
            store.get_snapshot_by_job(job_id)
        if reopen_hook is not None:
            reopen_hook(job_id, ledger.get_job(job_id))
        if adapters is not None:
            ledger.close()
            store.close()
            ledger, store = adapters()
            worker = ReportJobWorker(
                work_ledger=ledger,
                retry_policy=policy,
                execution_service=ReportJobExecutionService(
                    report_job_ledger=ledger,
                    capture_service=PortfolioReviewSnapshotCaptureService(
                        snapshot_store=store, job_ledger=ledger
                    ),
                    render_service=NoDocumentRender(),
                ),
            )
        if outcome == "revoked":
            authority.refused = True
        second = await worker.run_once(worker_id="reopened", max_items=1, lease_seconds=30)
        job = ledger.get_job(job_id)
        item = ledger.get_work_item_for_job(job_id)
        assert item.attempt_count == 2
        if outcome == "rotation":
            assert second.completed_count == 1
            assert job.status == "data_ready"
            assert sent[0]["headers"]["Authorization"] != sent[1]["headers"]["Authorization"]
        elif outcome == "revoked":
            assert job.status == "failed"
            assert job.failure_category == "validation_failed"
            assert job.retry_eligible is False
            assert job.failure_message == "Composite TWR invocation authority was refused."
            assert item.status == "completed"
            assert len(sent) == 1
            snapshot = store.get_snapshot_by_job(job_id)
            assert snapshot.snapshot_payload["capture_status"] == "failed"
            assert _stored_capture_failure(snapshot.snapshot_payload) == (
                "validation_failed",
                "Composite TWR invocation authority was refused.",
                False,
            )
            if adapters is not None:
                ledger.close()
                store.close()
                ledger, store = adapters()
            else:
                ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
                store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")
            if reopen_hook is not None:
                reopen_hook(job_id, job, snapshot)
            restored_service = PortfolioReviewSnapshotCaptureService(
                snapshot_store=store, job_ledger=ledger
            )
            restored = await restored_service.capture_for_job(ledger.get_job(job_id))
            assert restored == job
            assert restored.retry_eligible is False
            assert store.get_snapshot_by_job(job_id) == snapshot
            assert len(sent) == 1
            worker = ReportJobWorker(
                work_ledger=ledger,
                execution_service=ReportJobExecutionService(
                    report_job_ledger=ledger,
                    capture_service=restored_service,
                    render_service=NoDocumentRender(),
                ),
                retry_policy=policy,
            )
        else:
            assert second.failed_count == 1
            assert item.status == "failed"
            assert job.status == "failed"
            assert job.failure_category == "operator_intervention_required"
            with pytest.raises(ReportInputSnapshotNotFoundError):
                store.get_snapshot_by_job(job_id)
        third = await worker.run_once(worker_id="finished", max_items=1, lease_seconds=30)
        assert third.claimed_count == 0
        assert len(sent) == (1 if outcome == "revoked" else 2)
        events = ledger.list_status_events(job_id)
        failures = [
            event
            for event in events
            if event.event_type == "composite_twr_invocation_transient_failure"
        ]
        assert len(failures) == (2 if outcome == "exhausted" else 1)
        retained = job.model_dump_json() + " ".join(event.model_dump_json() for event in events)
        assert "must-not-escape" not in retained
        assert "synthetic-contract-flow-credential" not in retained
        assert "synthetic-contract-flow-credential" not in repr(authority.attempts)
        assert supplier["calls"] == []  # Only the explicit transport above was used.


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["rotation", "revoked", "exhausted"])
async def test_registered_worker_retry_rotation_revocation_exhaustion(
    tmp_path, monkeypatch, outcome
):
    await exercise_worker_retry(tmp_path, monkeypatch, outcome)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 502, 504, "transport"])
async def test_transient_status_and_transport_use_finite_worker_budget(
    tmp_path, monkeypatch, status
):
    await exercise_worker_retry(tmp_path, monkeypatch, "exhausted", status=status)
