from datetime import UTC, datetime

import pytest

from app.reporting_lineage.models import (
    ReportInputSnapshotCreateRequest,
    ReportUpstreamCallCreateRequest,
)
from app.reporting_lineage.store import (
    ReportInputSnapshotAlreadyCapturedError,
    ReportInputSnapshotNotFoundError,
    ReportInputSnapshotStore,
    canonical_json_dumps,
    compute_snapshot_hash,
)


def _request(**overrides: object) -> ReportInputSnapshotCreateRequest:
    payload = {
        "report_job_id": "rjob_123",
        "report_type": "portfolio_review",
        "report_data_contract_version": "v1",
        "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        "as_of_date": "2026-04-22",
        "snapshot_payload": {
            "report_id": "portfolio-review:PB_SG_GLOBAL_BAL_001:2026-04-22",
            "as_of_date": "2026-04-22",
            "sections": ["OVERVIEW", "PERFORMANCE"],
        },
        "snapshot_storage_ref": None,
        "supportability_status": "complete",
        "completeness_status": "complete",
        "lineage_summary": {"source_services": ["lotus-core", "lotus-performance"]},
        "captured_at": datetime(2026, 4, 22, 9, 0, 3, tzinfo=UTC),
        "correlation_id": "corr-portfolio-review-1",
        "trace_id": "trace-portfolio-review-1",
    }
    payload.update(overrides)
    return ReportInputSnapshotCreateRequest.model_validate(payload)


def test_canonical_json_dumps_is_stable_for_key_order_and_timestamps() -> None:
    left = {
        "b": 2,
        "a": {"z": 1, "ts": datetime(2026, 4, 22, 9, 0, 3, tzinfo=UTC)},
    }
    right = {
        "a": {"ts": datetime(2026, 4, 22, 9, 0, 3, tzinfo=UTC), "z": 1},
        "b": 2,
    }

    assert canonical_json_dumps(left) == canonical_json_dumps(right)
    assert compute_snapshot_hash(left) == compute_snapshot_hash(right)


def test_report_input_snapshot_store_creates_and_loads_snapshot(tmp_path) -> None:
    store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")

    created = store.create_snapshot(_request())
    loaded = store.get_snapshot(created.snapshot_id)
    loaded_by_job = store.get_snapshot_by_job("rjob_123")

    assert created.snapshot_id.startswith("rsnap_")
    assert created.snapshot_hash == compute_snapshot_hash(created.snapshot_payload)
    assert loaded == created
    assert loaded_by_job == created


def test_report_input_snapshot_store_is_idempotent_for_same_job_and_payload(tmp_path) -> None:
    store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")

    first = store.create_snapshot(_request())
    second = store.create_snapshot(_request())

    assert second == first


def test_report_input_snapshot_store_rejects_conflicting_rewrite(tmp_path) -> None:
    store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")
    store.create_snapshot(_request())

    with pytest.raises(
        ReportInputSnapshotAlreadyCapturedError,
        match="report_input_snapshot_already_captured",
    ):
        store.create_snapshot(
            _request(snapshot_payload={"report_id": "portfolio-review:changed", "sections": []})
        )


def test_report_input_snapshot_store_reports_missing_snapshot(tmp_path) -> None:
    store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")

    with pytest.raises(ReportInputSnapshotNotFoundError, match="report_input_snapshot_not_found"):
        store.get_snapshot("rsnap_missing")

    with pytest.raises(ReportInputSnapshotNotFoundError, match="report_input_snapshot_not_found"):
        store.get_snapshot_by_job("rjob_missing")


def test_report_input_snapshot_store_creates_and_lists_upstream_calls(tmp_path) -> None:
    store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")
    snapshot = store.create_snapshot(_request())

    created = store.create_upstream_calls(
        snapshot_id=snapshot.snapshot_id,
        calls=[
            ReportUpstreamCallCreateRequest(
                service_name="lotus-core",
                endpoint="/reporting/portfolio-summary/query",
                method="POST",
                contract_version="v1",
                request_hash="sha256:req",
                response_hash="sha256:resp",
                response_ref=None,
                status_code=200,
                latency_ms=184,
                supportability_status="complete",
                completeness_status="complete",
                failure_category="none",
                failure_message=None,
                captured_at=datetime(2026, 4, 22, 9, 0, 4, tzinfo=UTC),
                correlation_id="corr-portfolio-review-1",
                trace_id="trace-portfolio-review-1",
            )
        ],
    )

    assert created[0].snapshot_id == snapshot.snapshot_id
    assert store.list_upstream_calls(snapshot.snapshot_id)[0].service_name == "lotus-core"
    assert store.list_upstream_calls_by_job(snapshot.report_job_id)[0].endpoint.endswith(
        "/portfolio-summary/query"
    )


def test_report_input_snapshot_store_upstream_calls_are_idempotent_by_snapshot(tmp_path) -> None:
    store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")
    snapshot = store.create_snapshot(_request())
    request = ReportUpstreamCallCreateRequest(
        service_name="lotus-risk",
        endpoint="/analytics/risk/calculate",
        method="POST",
        contract_version="v1",
        request_hash="sha256:req",
        response_hash=None,
        response_ref=None,
        status_code=504,
        latency_ms=1000,
        supportability_status="unavailable",
        completeness_status="unavailable",
        failure_category="timeout",
        failure_message="Upstream request timed out before a complete response was returned.",
        captured_at=datetime(2026, 4, 22, 9, 0, 4, tzinfo=UTC),
        correlation_id="corr-portfolio-review-1",
        trace_id="trace-portfolio-review-1",
    )

    first = store.create_upstream_calls(snapshot_id=snapshot.snapshot_id, calls=[request])
    second = store.create_upstream_calls(snapshot_id=snapshot.snapshot_id, calls=[request])

    assert len(first) == 1
    assert second == first
