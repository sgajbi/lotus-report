from datetime import UTC, datetime

import pytest

from app.reporting_lineage.models import ReportInputSnapshotCreateRequest
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
