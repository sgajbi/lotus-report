"""Correction/replacement lineage: explicit outcomes, convergent recovery.

report#266's failure discipline, held one behaviour per test: every lineage
attempt leaves a durable event (recorded or pending, never silence), a
pending pair is re-attempted by settlement until Archive holds it, and the
stored new document is never disturbed by a pending linkage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

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
        return self.status_code, {"lifecycle_relationship_id": "life_test"}


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
