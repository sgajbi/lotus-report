"""Controlled orchestration ports prove no send on refusal, never live authority."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.reporting_jobs.custody_policy import CustodyRecoveryError, current_work_lease
from app.reporting_render.retained_offer import RetainedOfferOrchestration
from app.reporting_render.waiting import RenderWaiting


@pytest.fixture
def ports():
    custody = SimpleNamespace(
        authorize=Mock(),
        commit_offer=Mock(),
        load_offer=Mock(return_value=b"retained"),
        observe=Mock(return_value={"render_job_id": "render", "custody_initial_count": 1}),
        reserve=Mock(return_value=True),
        headers=Mock(return_value={"X-Tenant-Id": "tenant"}),
        disposition=Mock(),
        record_owner_due=Mock(return_value=True),
        block=Mock(),
        confirm_final=AsyncMock(),
        bindings=SimpleNamespace(recovery_action=Mock(return_value=("wait", None))),
    )
    job = SimpleNamespace(
        job_id="job", tenant_id="tenant", region="APAC", custody_epoch=3, status="rendering"
    )
    ledger = SimpleNamespace(custody=custody, get_job=Mock(return_value=job))
    client = SimpleNamespace(
        submit_retained_offer=AsyncMock(return_value=(202, {})),
        read_retained_status=AsyncMock(
            return_value=(200, {"status": "rendered", "render_job_id": "render"})
        ),
        retry_retained_custody=AsyncMock(return_value=(202, {})),
    )
    token = current_work_lease.set(("work", "lease"))
    try:
        yield RetainedOfferOrchestration(ledger=ledger, client=client), job, custody, client
    finally:
        current_work_lease.reset(token)


@pytest.mark.asyncio
async def test_orchestration_without_worker_lease_never_authorizes_or_sends(ports):
    orchestration, job, custody, client = ports
    token = current_work_lease.set(None)
    try:
        with pytest.raises(CustodyRecoveryError, match="custody_worker_lease_required"):
            await orchestration.advance(job, Mock())
    finally:
        current_work_lease.reset(token)
    custody.authorize.assert_not_called()
    client.submit_retained_offer.assert_not_awaited()


@pytest.mark.parametrize(
    "fault,code",
    [
        ("identity", "custody_identity_conflict"),
        ("binding", "custody_authority_unavailable"),
        ("action", "custody_owner_action_invalid"),
    ],
)
@pytest.mark.asyncio
async def test_rendered_response_refusal_does_not_retry_or_settle(ports, fault, code):
    orchestration, job, custody, client = ports
    if fault == "identity":
        client.read_retained_status.return_value[1]["render_job_id"] = "other"
    elif fault == "binding":
        custody.bindings = None
    else:
        custody.bindings.recovery_action.return_value = ("unknown", None)
    with pytest.raises(CustodyRecoveryError, match=code):
        await orchestration.advance(job, Mock())
    custody.disposition.assert_not_called()
    custody.confirm_final.assert_not_awaited()
    client.retry_retained_custody.assert_not_awaited()


@pytest.mark.parametrize(
    "action,due,allowed",
    [
        ("wait", None, True),
        ("blocked", None, True),
        ("verify", None, True),
        ("retry", None, True),
        ("retry", datetime.now(UTC), True),
        ("wait", datetime.now(UTC), False),
        ("retry", None, False),
    ],
)
@pytest.mark.asyncio
async def test_owner_action_controls_retry_block_and_independent_verification(
    ports, action, due, allowed
):
    orchestration, job, custody, client = ports
    custody.bindings.recovery_action.return_value = (action, due)
    if due is not None:
        custody.record_owner_due.return_value = allowed
    else:
        custody.reserve.side_effect = [True, allowed]
    result = await orchestration.advance(
        job, Mock(side_effect=AssertionError("recovery rebuilt offer"))
    )
    assert client.retry_retained_custody.await_count == int(action == "retry" and allowed)
    assert custody.block.call_count == int(action == "blocked")
    assert custody.confirm_final.await_count == int(action == "verify")
    assert isinstance(result, RenderWaiting) if action != "verify" else result is job
    if due is not None:
        assert custody.record_owner_due.call_args.kwargs["due"] == due
    if not allowed:
        custody.disposition.assert_not_called()


@pytest.mark.parametrize("phase", ["initial", "poll", "404_retry", "nonrendered"])
@pytest.mark.asyncio
async def test_reservation_refusal_never_sends_or_rebuilds(ports, phase):
    orchestration, job, custody, client = ports
    custody.reserve.return_value = False
    if phase == "initial":
        custody.observe.return_value["custody_initial_count"] = 0
    elif phase == "404_retry":
        custody.reserve.side_effect = [True, False]
        client.read_retained_status.return_value = (404, {})
    elif phase == "nonrendered":
        custody.reserve.return_value = True
        client.read_retained_status.return_value = (503, {"status": "rendering"})
    result = await orchestration.advance(
        job, Mock(side_effect=AssertionError("recovery rebuilt offer"))
    )
    assert isinstance(result, RenderWaiting)
    client.submit_retained_offer.assert_not_awaited()
    client.retry_retained_custody.assert_not_awaited()
    assert custody.disposition.call_count == int(phase == "nonrendered")


@pytest.mark.asyncio
async def test_data_ready_commit_precedes_loading_and_sending_retained_bytes(ports):
    orchestration, job, custody, client = ports
    job.status = "data_ready"
    custody.observe.return_value["custody_initial_count"] = 0
    events = Mock()
    events.attach_mock(custody.commit_offer, "commit_offer")
    events.attach_mock(custody.load_offer, "load_offer")
    events.attach_mock(client.submit_retained_offer, "submit_retained_offer")
    package = Mock(return_value={"render_job_id": "render"})
    await orchestration.advance(job, package)
    assert [call[0] for call in events.mock_calls] == [
        "commit_offer",
        "load_offer",
        "submit_retained_offer",
    ]
    package.assert_called_once_with()
    custody.commit_offer.assert_called_once()
    assert client.submit_retained_offer.call_args.kwargs["body"] == b"retained"
    custody.disposition.assert_called_once()
