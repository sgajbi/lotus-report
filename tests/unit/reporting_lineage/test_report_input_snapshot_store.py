from datetime import UTC, datetime

import pytest

from app.reporting_lineage.models import (
    ReportInputSnapshotCreateRequest,
    ReportUpstreamCallCreateRequest,
)
from app.reporting_lineage.store import (
    ReportInputSnapshotAlreadyCapturedError,
    ReportInputSnapshotLineageConflictError,
    ReportInputSnapshotNotFoundError,
    ReportInputSnapshotStore,
    _date_from_value,
    _dt_from_text,
    _dt_to_text,
    _normalize_json_value,
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


def _upstream_call_request(
    *,
    service_name: str = "lotus-core",
    endpoint: str = "/reporting/portfolio-summary/query",
) -> ReportUpstreamCallCreateRequest:
    return ReportUpstreamCallCreateRequest(
        service_name=service_name,
        endpoint=endpoint,
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


def test_report_input_snapshot_store_creates_capture_atomically(tmp_path) -> None:
    store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")

    snapshot, calls = store.create_capture(
        snapshot=_request(lineage_summary={"source_services": ["lotus-core"], "call_count": 1}),
        upstream_calls=[_upstream_call_request()],
    )

    assert store.get_snapshot_by_job("rjob_123") == snapshot
    assert calls == store.list_upstream_calls(snapshot.snapshot_id)
    assert [call.service_name for call in calls] == ["lotus-core"]


def test_report_input_snapshot_store_rolls_back_capture_when_lineage_write_fails(
    tmp_path, monkeypatch
) -> None:
    database_path = tmp_path / "snapshots.sqlite3"
    store = ReportInputSnapshotStore(database_path)
    original_insert = store._insert_upstream_calls

    def _fail_lineage_write(*_args, **_kwargs) -> None:
        raise RuntimeError("injected_lineage_write_failure")

    monkeypatch.setattr(store, "_insert_upstream_calls", _fail_lineage_write)
    with pytest.raises(RuntimeError, match="injected_lineage_write_failure"):
        store.create_capture(
            snapshot=_request(),
            upstream_calls=[_upstream_call_request()],
        )

    restarted_store = ReportInputSnapshotStore(database_path)
    with pytest.raises(ReportInputSnapshotNotFoundError, match="report_input_snapshot_not_found"):
        restarted_store.get_snapshot_by_job("rjob_123")

    monkeypatch.setattr(store, "_insert_upstream_calls", original_insert)
    snapshot, calls = restarted_store.create_capture(
        snapshot=_request(),
        upstream_calls=[_upstream_call_request()],
    )
    assert snapshot.report_job_id == "rjob_123"
    assert len(calls) == 1


def test_report_input_snapshot_store_restores_missing_lineage_for_matching_snapshot(
    tmp_path,
) -> None:
    store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")
    existing = store.create_snapshot(
        _request(lineage_summary={"source_services": ["lotus-core"], "call_count": 0})
    )

    snapshot, calls = store.create_capture(
        snapshot=_request(lineage_summary={"source_services": ["lotus-core"], "call_count": 1}),
        upstream_calls=[_upstream_call_request()],
    )

    assert snapshot.snapshot_id == existing.snapshot_id
    assert snapshot.lineage_summary["call_count"] == 1
    assert len(calls) == 1


def test_report_input_snapshot_store_rejects_conflicting_immutable_lineage(tmp_path) -> None:
    store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")
    store.create_capture(
        snapshot=_request(),
        upstream_calls=[_upstream_call_request()],
    )

    with pytest.raises(
        ReportInputSnapshotLineageConflictError,
        match="report_input_snapshot_lineage_conflict",
    ):
        store.create_capture(
            snapshot=_request(),
            upstream_calls=[
                _upstream_call_request(
                    service_name="lotus-risk",
                    endpoint="/analytics/risk/calculate",
                )
            ],
        )


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


def test_report_input_snapshot_store_normalizes_datetime_payloads(tmp_path) -> None:
    store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")

    created = store.create_snapshot(
        _request(
            snapshot_payload={
                "captured_window": {
                    "started_at": datetime(2026, 4, 22, 9, 0, tzinfo=UTC),
                    "ended_at": datetime(2026, 4, 22, 9, 5, tzinfo=UTC),
                }
            },
            lineage_summary={
                "last_source_refresh_at": datetime(2026, 4, 22, 8, 59, tzinfo=UTC),
            },
        )
    )

    assert created.snapshot_payload["captured_window"]["started_at"] == "2026-04-22T09:00:00Z"
    assert created.snapshot_payload["captured_window"]["ended_at"] == "2026-04-22T09:05:00Z"
    assert created.lineage_summary["last_source_refresh_at"] == "2026-04-22T08:59:00Z"


def test_report_input_snapshot_store_helper_normalizers_cover_all_value_types(tmp_path) -> None:
    store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")
    store.create_snapshot(_request())

    naive = datetime(2026, 4, 22, 9, 0, 3)
    assert _normalize_json_value(naive) == "2026-04-22T09:00:03Z"
    assert (
        _normalize_json_value(datetime(2026, 4, 22, 9, 0, 3, tzinfo=UTC)) == "2026-04-22T09:00:03Z"
    )
    assert _normalize_json_value(_request().as_of_date) == "2026-04-22"
    assert _normalize_json_value((1, 2, 3)) == [1, 2, 3]
    assert _dt_to_text(None) is None
    assert _dt_to_text(naive) == "2026-04-22T09:00:03Z"
    assert _dt_from_text(None) is None
    assert _date_from_value("2026-04-22").isoformat() == "2026-04-22"


def test_report_input_snapshot_store_rejects_upstream_calls_for_missing_snapshot(tmp_path) -> None:
    store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")

    assert store.create_upstream_calls(snapshot_id="rsnap_missing", calls=[]) == []
    with pytest.raises(ReportInputSnapshotNotFoundError, match="report_input_snapshot_not_found"):
        store.create_upstream_calls(
            snapshot_id="rsnap_missing",
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
                    latency_ms=100,
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


def test_report_input_snapshot_store_roundtrips_the_revision_binding(tmp_path) -> None:
    """The revision binding persists beside the payload and reads back
    verbatim - and a snapshot created without one stays honestly NULL."""

    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    bound = store.create_snapshot(
        _request(
            report_job_id="rjob_bound",
            report_revision_id="rrv2_roundtrip",
            series_digest="series-digest-rt",
            source_revision_digest="vector-digest-rt",
            factual_content_digest="sha256:facts-rt",
            factual_boundary_version="fb1",
            source_revision_vector={
                "coverage": "partial",
                "revisions": [{"source_service": "lotus-core", "restatement_version": "r1"}],
            },
        )
    )
    unbound = store.create_snapshot(_request(report_job_id="rjob_unbound"))

    loaded = store.get_snapshot_by_job("rjob_bound")
    assert loaded.report_revision_id == "rrv2_roundtrip"
    assert loaded.series_digest == "series-digest-rt"
    assert loaded.source_revision_digest == "vector-digest-rt"
    assert loaded.factual_content_digest == "sha256:facts-rt"
    assert loaded.factual_boundary_version == "fb1"
    assert loaded.source_revision_vector == {
        "coverage": "partial",
        "revisions": [{"source_service": "lotus-core", "restatement_version": "r1"}],
    }
    assert bound.report_revision_id == "rrv2_roundtrip"
    assert unbound.report_revision_id is None
    assert unbound.source_revision_vector is None
