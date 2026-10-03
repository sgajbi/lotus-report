"""Correction/replacement lineage: explicit outcomes, convergent recovery.

report#266's failure discipline, held one behaviour per test: every lineage
attempt leaves a durable event (recorded or pending, never silence), a
pending pair is re-attempted by settlement until Archive holds it, and the
stored new document is never disturbed by a pending linkage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from app.clients.archive_client import ArchiveClient
from app.reporting_jobs.models import ReportCallerContext
from app.reporting_render.archive_lineage import (
    LINEAGE_PENDING_EVENT,
    LINEAGE_RECORDED_EVENT,
    LINEAGE_REFUSED_EVENT,
    reconcile_pending_archive_lineage,
    record_archive_lineage,
    settle_pending_archive_lineage,
)


def _caller() -> ReportCallerContext:
    return ReportCallerContext(
        triggered_by="advisor-123",
        caller_application="lotus-workbench",
        tenant_id="tenant-sg",
        region="APAC",
        correlation_id="corr-lineage",
        trace_id="trace-lineage",
    )


@dataclass
class _LifecycleClient:
    status_code: int = 201
    calls: list[dict[str, Any]] = field(default_factory=list)
    raise_error: bool = False

    async def record_lifecycle_transition(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        self.calls.append(kwargs)
        if self.raise_error:
            raise RuntimeError("archive connection reset")
        return self.status_code, {
            "lifecycle_relationship_id": "life_test",
            "source_document_id": kwargs["source_document_id"],
            "target_document_id": kwargs["target_document_id"],
            "transition_type": kwargs["transition_type"],
            "transition_reason": "Original accepted reason",
            "transition_reason_code": "report_correction",
            "requested_by": "original-actor",
            "requested_at": "2026-01-09T00:00:00Z",
            "current_document_id": "doc_later_in_chain",
        }


@dataclass
class _EventLedger:
    events: list[Any] = field(default_factory=list)
    keys: set[str] = field(default_factory=set)

    def append_job_event(self, **kwargs: Any) -> bool:
        key = kwargs.get("event_idempotency_key")
        if key and kwargs.get("skip_if_idempotency_key_exists") and key in self.keys:
            return False
        if key:
            self.keys.add(key)

        class _Event:
            event_type = kwargs["event_type"]
            event_payload = kwargs.get("event_payload") or {}

        self.events.append(_Event())
        return True

    def list_status_events(self, job_id: str) -> list[Any]:
        return list(self.events)


@pytest.mark.asyncio
async def test_a_recorded_linkage_leaves_the_recorded_event() -> None:
    client = _LifecycleClient()
    ledger = _EventLedger()

    recorded = await record_archive_lineage(
        archive_client=client,
        ledger=ledger,
        event_job_id="rjob_1",
        source_document_id="doc_old",
        target_document_id="doc_new",
        transition_type="correct",
        transition_reason="Rerender correction rrnd_1",
        caller_context=_caller(),
    )

    assert recorded is True
    assert [event.event_type for event in ledger.events] == [LINEAGE_RECORDED_EVENT]
    assert client.calls[0]["transition_type"] == "correct"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["status", "exception"])
async def test_a_failed_linkage_is_explicit_never_silent(failure: str) -> None:
    client = _LifecycleClient(
        status_code=503 if failure == "status" else 201,
        raise_error=failure == "exception",
    )
    ledger = _EventLedger()

    recorded = await record_archive_lineage(
        archive_client=client,
        ledger=ledger,
        event_job_id="rjob_1",
        source_document_id="doc_old",
        target_document_id="doc_new",
        transition_type="supersede",
        transition_reason="Regenerate replacement rjob_2",
        caller_context=_caller(),
    )

    assert recorded is False
    assert [event.event_type for event in ledger.events] == [LINEAGE_PENDING_EVENT]
    payload = ledger.events[0].event_payload
    assert payload["source_document_id"] == "doc_old"
    assert payload["target_document_id"] == "doc_new"
    assert payload["transition_type"] == "supersede"


@pytest.mark.asyncio
async def test_settlement_retries_pending_pairs_until_archive_holds_them() -> None:
    client = _LifecycleClient(status_code=503)
    ledger = _EventLedger()
    await record_archive_lineage(
        archive_client=client,
        ledger=ledger,
        event_job_id="rjob_1",
        source_document_id="doc_old",
        target_document_id="doc_new",
        transition_type="correct",
        transition_reason="Rerender correction rrnd_1",
        caller_context=_caller(),
    )
    assert len(client.calls) == 1

    # Archive recovers; settlement re-attempts the exact pending pair.
    client.status_code = 201
    await settle_pending_archive_lineage(
        archive_client=client,
        ledger=ledger,
        event_job_id="rjob_1",
        caller_context=_caller(),
    )

    assert len(client.calls) == 2
    assert client.calls[1]["source_document_id"] == "doc_old"
    assert client.calls[1]["target_document_id"] == "doc_new"
    assert [event.event_type for event in ledger.events] == [
        LINEAGE_PENDING_EVENT,
        LINEAGE_RECORDED_EVENT,
    ]

    # A settled pair is not re-attempted again.
    await settle_pending_archive_lineage(
        archive_client=client,
        ledger=ledger,
        event_job_id="rjob_1",
        caller_context=_caller(),
    )
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_settlement_without_pending_pairs_makes_no_calls() -> None:
    client = _LifecycleClient()
    ledger = _EventLedger()

    await settle_pending_archive_lineage(
        archive_client=client,
        ledger=ledger,
        event_job_id="rjob_1",
        caller_context=_caller(),
    )

    assert client.calls == []


@pytest.mark.asyncio
async def test_a_contract_refusal_is_terminal_and_surfaced_not_retried_forever() -> None:
    client = _LifecycleClient(status_code=422)
    ledger = _EventLedger()

    recorded = await record_archive_lineage(
        archive_client=client,
        ledger=ledger,
        event_job_id="rjob_1",
        source_document_id="doc_old",
        target_document_id="doc_new",
        transition_type="supersede",
        transition_reason="Regenerate replacement rjob_2",
        caller_context=_caller(),
    )

    assert recorded is False
    assert [event.event_type for event in ledger.events] == [LINEAGE_REFUSED_EVENT]
    assert ledger.events[0].event_payload["status_code"] == 422

    # Settlement never re-attempts a terminally refused pair.
    await settle_pending_archive_lineage(
        archive_client=client,
        ledger=ledger,
        event_job_id="rjob_1",
        caller_context=_caller(),
    )
    assert len(client.calls) == 1


def _real_job(tmp_path, idempotency_key: str):
    import sys

    sys.path.insert(0, str(tmp_path.parents[0]))
    from test_service import _caller as job_caller
    from test_service import _job_request

    from app.reporting_jobs.ledger import ReportJobLedger

    ledger = ReportJobLedger(tmp_path / f"jobs-{idempotency_key}.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=job_caller(),
        idempotency_key=idempotency_key,
    )
    return ledger, job


@pytest.mark.asyncio
async def test_a_transient_archive_outage_self_heals_without_another_correction(tmp_path) -> None:
    """The steering's evaluation condition, on the REAL durable ledger: a
    lineage pair left pending by an Archive outage converges through the
    bounded reconciliation pass alone - nobody orders another rerender or
    regenerate - and a settled ledger stops feeding the pass entirely."""

    ledger, job = _real_job(tmp_path, "idem-lineage-heal")
    outage = _LifecycleClient(status_code=503)
    await record_archive_lineage(
        archive_client=outage,
        ledger=ledger,
        event_job_id=job.job_id,
        source_document_id="doc_old",
        target_document_id="doc_new",
        transition_type="correct",
        transition_reason="Rerender correction rrnd_1",
        caller_context=_caller(),
    )
    pending = ledger.list_pending_archive_lineage(limit=10)
    assert [row.job_id for row in pending] == [job.job_id]
    assert pending[0].oldest_created_at is not None

    recovered = _LifecycleClient(status_code=201)
    result = await reconcile_pending_archive_lineage(
        archive_client=recovered,
        ledger=ledger,
        limit=10,
    )

    assert result["outstanding_jobs"] == 1
    assert recovered.calls[0]["source_document_id"] == "doc_old"
    assert recovered.calls[0]["target_document_id"] == "doc_new"
    event_types = [event.event_type for event in ledger.list_status_events(job.job_id)]
    assert LINEAGE_RECORDED_EVENT in event_types
    # Converged: the settled pair never re-enters the pass.
    assert ledger.list_pending_archive_lineage(limit=10) == []
    again = await reconcile_pending_archive_lineage(
        archive_client=recovered,
        ledger=ledger,
        limit=10,
    )
    assert again["outstanding_jobs"] == 0
    assert len(recovered.calls) == 1


@pytest.mark.asyncio
async def test_a_refused_pair_leaves_the_reconciliation_feed(tmp_path) -> None:
    ledger, job = _real_job(tmp_path, "idem-lineage-refused")
    refusing = _LifecycleClient(status_code=503)
    await record_archive_lineage(
        archive_client=refusing,
        ledger=ledger,
        event_job_id=job.job_id,
        source_document_id="doc_old",
        target_document_id="doc_new",
        transition_type="supersede",
        transition_reason="Regenerate replacement",
        caller_context=_caller(),
    )
    assert ledger.list_pending_archive_lineage(limit=10) != []

    # Archive recovers but refuses the pair on contract grounds: terminal,
    # surfaced, and never fed back into the pass.
    refusing.status_code = 404
    await reconcile_pending_archive_lineage(archive_client=refusing, ledger=ledger, limit=10)

    event_types = [event.event_type for event in ledger.list_status_events(job.job_id)]
    assert "job_archive_lineage_refused" in event_types
    assert ledger.list_pending_archive_lineage(limit=10) == []


@pytest.mark.asyncio
async def test_the_reconciliation_pass_is_bounded(tmp_path) -> None:
    ledger, job_a = _real_job(tmp_path, "idem-lineage-a")
    outage = _LifecycleClient(status_code=503)
    for suffix in ("one", "two"):
        await record_archive_lineage(
            archive_client=outage,
            ledger=ledger,
            event_job_id=job_a.job_id,
            source_document_id=f"doc_{suffix}",
            target_document_id=f"doc_{suffix}_new",
            transition_type="correct",
            transition_reason="Rerender correction",
            caller_context=_caller(),
        )

    recovered = _LifecycleClient(status_code=201)
    result = await reconcile_pending_archive_lineage(
        archive_client=recovered,
        ledger=ledger,
        limit=0,
    )

    assert result["outstanding_jobs"] == 0
    assert recovered.calls == []


@pytest.mark.asyncio
async def test_the_worker_pass_survives_a_failing_maintenance_hook() -> None:
    from app.reporting_jobs.process import ReportJobWorkerProcess, ReportJobWorkerProcessConfig
    from app.reporting_jobs.work_queue import ReportJobWorkRetryPolicy
    from app.reporting_jobs.worker import ReportJobWorkerRunResult

    class _Worker:
        async def run_once(self, *, worker_id, max_items, lease_seconds):
            return ReportJobWorkerRunResult(
                worker_id=worker_id,
                claimed_count=0,
                completed_count=0,
                retry_pending_count=0,
                failed_count=0,
                outcomes=[],
            )

    calls = {"count": 0}

    async def _broken_maintenance() -> None:
        calls["count"] += 1
        raise RuntimeError("archive unreachable")

    process = ReportJobWorkerProcess(
        worker=_Worker(),
        config=ReportJobWorkerProcessConfig(
            worker_id="w-test",
            interval_seconds=0.0,
            max_items_per_pass=1,
            lease_seconds=5,
            retry_policy=ReportJobWorkRetryPolicy(),
        ),
        maintenance=_broken_maintenance,
    )

    # A maintenance failure is logged, never fatal: the pass completes.
    await process.run(max_iterations=2)
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_reconcile_tolerates_rows_without_timestamps() -> None:
    from types import SimpleNamespace

    class _Ledger(_EventLedger):
        def list_pending_archive_lineage(self, *, limit):
            return [SimpleNamespace(job_id="rjob_x", oldest_created_at=None)]

        def get_job(self, job_id):
            return SimpleNamespace(
                job_id=job_id,
                tenant_id="tenant-sg",
                region="APAC",
                correlation_id="corr-x",
                trace_id="trace-x",
            )

    client = _LifecycleClient()
    result = await reconcile_pending_archive_lineage(
        archive_client=client,
        ledger=_Ledger(),
        limit=5,
    )

    assert result["outstanding_jobs"] == 1
    assert result["oldest_age_seconds"] is None


def _acknowledgement(transition):
    return {
        "lifecycle_relationship_id": "relationship-original",
        "source_document_id": "doc_old",
        "target_document_id": "doc_new",
        "transition_type": transition,
        "transition_reason": "Original reason before this retry",
        "transition_reason_code": "report_correction",
        "requested_by": "original-actor",
        "requested_at": "2026-01-09T00:00:00Z",
        "current_document_id": "doc_later_in_chain",
    }


def _transport_client(monkeypatch, *, status, payload, calls):
    original_client = httpx.AsyncClient

    def handler(request):
        import json

        calls.append(request)
        assert request.url.path in {"/documents/doc_old/correct", "/documents/doc_old/supersede"}
        assert json.loads(request.content) == {
            "target_document_id": "doc_new",
            "transition_reason": "Retry reason",
        }
        assert request.headers["X-Tenant-Id"] == "tenant-sg"
        assert request.headers["X-Region"] == "APAC"
        assert request.headers["X-Actor-Id"] == "advisor-123"
        assert request.headers["X-Correlation-ID"] == "corr-lineage"
        assert request.headers["X-Trace-ID"] == "trace-lineage"
        if payload == "html":
            return httpx.Response(status, text="<html>untrusted-source-detail</html>")
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(handler)),
    )
    return ArchiveClient(
        base_url="https://archive.invalid",
        timeout_seconds=3,
        max_retries=0,
        retry_backoff_seconds=0,
    )


async def _record_wire(client, ledger, transition, job_id="rjob_1"):
    return await record_archive_lineage(
        archive_client=client,
        ledger=ledger,
        event_job_id=job_id,
        source_document_id="doc_old",
        target_document_id="doc_new",
        transition_type=transition,
        transition_reason="Retry reason",
        caller_context=_caller(),
    )


@pytest.mark.parametrize("transition", ["correct", "supersede"])
@pytest.mark.parametrize("status", [200, 201])
@pytest.mark.parametrize(
    "fault",
    [
        "empty",
        "missing-id",
        "null-id",
        "blank-id",
        "bool-id",
        "list-id",
        "oversize-id",
        "foreign-source",
        "foreign-target",
        "foreign-transition",
        "html",
        "list",
        "null",
    ],
)
async def test_successful_transport_requires_matching_archive_acknowledgement(
    monkeypatch, transition, status, fault
):
    payload = _acknowledgement(transition)
    if fault == "empty":
        payload = {}
    elif fault == "missing-id":
        payload.pop("lifecycle_relationship_id")
    elif fault in {"null-id", "blank-id", "bool-id", "list-id"}:
        payload["lifecycle_relationship_id"] = {
            "null-id": None,
            "blank-id": " \t\r\n",
            "bool-id": True,
            "list-id": ["id"],
        }[fault]
    elif fault.startswith("foreign-"):
        key = {
            "foreign-source": "source_document_id",
            "foreign-target": "target_document_id",
            "foreign-transition": "transition_type",
        }[fault]
        payload[key] = "unrelated"
    elif fault == "oversize-id":
        payload["lifecycle_relationship_id"] = "a" * 257
    elif fault == "html":
        payload = "html"
    elif fault == "list":
        payload = [_acknowledgement(transition)]
    else:
        payload = None
    calls = []
    client = _transport_client(monkeypatch, status=status, payload=payload, calls=calls)
    ledger = _EventLedger()
    assert await _record_wire(client, ledger, transition) is False
    assert len(calls) == 1
    assert [event.event_type for event in ledger.events] == [LINEAGE_PENDING_EVENT]
    assert (
        ledger.events[0].event_payload["reason_code"] == "archive_lineage_acknowledgement_invalid"
    )
    assert ledger.events[0].event_payload["status_code"] == status
    assert "untrusted-source-detail" not in str(ledger.events[0].event_payload)


@pytest.mark.parametrize("transition", ["correct", "supersede"])
@pytest.mark.parametrize("status", [200, 201])
async def test_matching_original_acknowledgement_accepts_replay_after_chain_advances(
    monkeypatch, transition, status
):
    calls = []
    client = _transport_client(
        monkeypatch, status=status, payload=_acknowledgement(transition), calls=calls
    )
    ledger = _EventLedger()
    assert await _record_wire(client, ledger, transition) is True
    assert len(calls) == 1
    assert [event.event_type for event in ledger.events] == [LINEAGE_RECORDED_EVENT]


async def test_confirmed_event_retains_only_validated_acknowledgement_identity(monkeypatch):
    calls = []
    payload = _acknowledgement("correct") | {"raw_payload": "untrusted-source-detail"}
    client = _transport_client(monkeypatch, status=201, payload=payload, calls=calls)
    ledger = _EventLedger()
    assert await _record_wire(client, ledger, "correct") is True
    assert ledger.events[0].event_payload == {
        "source_document_id": "doc_old",
        "target_document_id": "doc_new",
        "transition_type": "correct",
        "lifecycle_relationship_id": "relationship-original",
    }


@pytest.mark.parametrize(
    "status,event_type",
    [
        (400, LINEAGE_REFUSED_EVENT),
        (403, LINEAGE_REFUSED_EVENT),
        (503, LINEAGE_PENDING_EVENT),
    ],
)
async def test_archive_wire_refusal_and_transient_outcomes_remain_distinct(
    monkeypatch, status, event_type
):
    calls = []
    client = _transport_client(
        monkeypatch, status=status, payload={"detail": "untrusted-source-detail"}, calls=calls
    )
    ledger = _EventLedger()
    assert await _record_wire(client, ledger, "correct") is False
    assert len(calls) == 1
    assert [event.event_type for event in ledger.events] == [event_type]
    assert "untrusted-source-detail" not in str(ledger.events[0].event_payload)


async def test_relationship_identifier_budget_keeps_valid_boundary(monkeypatch):
    calls = []
    payload = _acknowledgement("correct") | {"lifecycle_relationship_id": "a" * 256}
    client = _transport_client(monkeypatch, status=201, payload=payload, calls=calls)
    ledger = _EventLedger()
    assert await _record_wire(client, ledger, "correct") is True
    assert ledger.events[0].event_payload["lifecycle_relationship_id"] == "a" * 256
