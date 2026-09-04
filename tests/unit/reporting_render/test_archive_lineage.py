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
