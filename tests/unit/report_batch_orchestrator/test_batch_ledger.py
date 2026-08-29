from __future__ import annotations

from uuid import uuid4

import pytest

from app.report_batch_orchestrator.ledger import (
    BatchIdempotencyConflictError,
    MissingBatchIdempotencyKeyError,
    ReportBatchLedger,
    _json_dict,
)
from app.report_batch_orchestrator.models import (
    BatchCreateRequest,
    BatchRetryPolicy,
    PortfolioBatchCandidate,
)
from app.report_batch_orchestrator.selector import BatchSelectorValidationError
from app.reporting_jobs.models import ReportCallerContext


def _caller() -> ReportCallerContext:
    suffix = uuid4().hex
    return ReportCallerContext(
        triggered_by="advisor-123",
        caller_application="lotus-gateway",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="advisor",
        correlation_id=f"corr-batch-{suffix}",
        trace_id=f"trace-batch-{suffix}",
    )


def _candidate(
    portfolio_id: str,
    *,
    tenant_id: str = "tenant-sg",
    region: str = "APAC",
    active: bool = True,
    selected: bool = False,
) -> PortfolioBatchCandidate:
    return PortfolioBatchCandidate(
        portfolio_id=portfolio_id,
        tenant_id=tenant_id,
        region=region,
        active=active,
        selected=selected,
    )


def _explicit_request(*portfolio_ids: str) -> BatchCreateRequest:
    return BatchCreateRequest(
        selector_mode="explicit_portfolio_list",
        portfolio_ids=list(portfolio_ids),
        source_candidates=[_candidate(portfolio_id) for portfolio_id in portfolio_ids],
        as_of_date="2026-04-22",
        requested_output_formats=["pdf"],
        reporting_currency="USD",
        options={"sections": ["OVERVIEW", "PERFORMANCE"]},
    )


def test_explicit_portfolio_list_materializes_items_in_request_order(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    caller = _caller()
    request = _explicit_request("PB_SG_GLOBAL_BAL_002", "PB_SG_GLOBAL_BAL_001")

    batch = ledger.create_batch(
        request=request,
        caller_context=caller,
        idempotency_key="batch-explicit-order",
    )

    assert batch.status == "materialized"
    assert batch.item_count == 2
    assert batch.materialized_portfolio_ids == [
        "PB_SG_GLOBAL_BAL_002",
        "PB_SG_GLOBAL_BAL_001",
    ]
    assert [item.item_position for item in batch.items] == [1, 2]
    assert [item.portfolio_id for item in batch.items] == batch.materialized_portfolio_ids
    assert all(
        item.item_idempotency_key.startswith("batch-explicit-order:") for item in batch.items
    )


def test_in_memory_schema_initialization_and_missing_lookup_are_bounded(tmp_path) -> None:
    assert ReportBatchLedger(":memory:") is not None

    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    batch = ledger.create_batch(
        request=_explicit_request("PB_SG_GLOBAL_BAL_001"),
        caller_context=_caller(),
        idempotency_key="batch-memory",
    )

    assert batch.item_count == 1
    with ledger._connect() as connection:
        with pytest.raises(ValueError, match="report_batch_not_found"):
            ledger._load_batch(connection, "rbch_missing")


def test_get_batch_item_returns_single_item_and_raises_not_founds(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    request = _explicit_request("PB_SG_GLOBAL_BAL_001", "PB_SG_GLOBAL_BAL_002")
    batch = ledger.create_batch(
        request=request,
        caller_context=_caller(),
        idempotency_key="batch-item-lookup",
    )
    item = ledger.get_batch_item(batch.batch_id, batch.items[1].batch_item_id)

    assert item.batch_item_id == batch.items[1].batch_item_id
    assert item.item_position == 2
    assert item.portfolio_id == "PB_SG_GLOBAL_BAL_002"
    assert item.status == "materialized"

    with pytest.raises(ValueError, match="report_batch_not_found"):
        ledger.get_batch_item(f"rbch_missing_{uuid4().hex}", batch.items[0].batch_item_id)

    with pytest.raises(ValueError, match="report_batch_item_not_found"):
        ledger.get_batch_item(batch.batch_id, "rbci_missing_item")


def test_mark_item_succeeded_and_failed_reject_stale_or_missing_items(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    batch = ledger.create_batch(
        request=_explicit_request("PB_SG_GLOBAL_BAL_001"),
        caller_context=_caller(),
        idempotency_key="batch-stale-item-transition",
    )
    item = batch.items[0]

    with pytest.raises(ValueError, match="report_batch_item_not_found"):
        ledger.mark_item_succeeded(
            batch_item_id=item.batch_item_id,
            report_job_id="rjob_not_linked",
        )
    with pytest.raises(ValueError, match="report_batch_item_not_found"):
        ledger.mark_item_failed(
            batch_item_id="rbit_missing",
            error_category="upstream_data_failed",
            error_summary="Missing item.",
            retryable=True,
        )


def test_relink_failed_item_for_replay_is_idempotent_and_enforces_retry_ceiling(
    tmp_path,
) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    batch = ledger.create_batch(
        request=_explicit_request("PB_SG_GLOBAL_BAL_001"),
        caller_context=_caller(),
        idempotency_key="batch-replay-ledger-edges",
    )
    item = batch.items[0]
    failed_item = ledger.mark_item_failed(
        batch_item_id=item.batch_item_id,
        error_category="upstream_data_failed",
        error_summary="Upstream timeout.",
        retryable=True,
        retry_policy=BatchRetryPolicy(max_attempts=3),
    )

    replayed = ledger.relink_failed_item_for_replay(
        batch_id=batch.batch_id,
        batch_item_id=failed_item.batch_item_id,
        replayed_report_job_id="rjob_replay_1",
        retry_policy=BatchRetryPolicy(max_attempts=3),
    )
    same_replay = ledger.relink_failed_item_for_replay(
        batch_id=batch.batch_id,
        batch_item_id=failed_item.batch_item_id,
        replayed_report_job_id="rjob_replay_1",
        retry_policy=BatchRetryPolicy(max_attempts=3),
    )

    assert replayed.status == "waiting_on_report_job"
    assert same_replay == replayed
    with pytest.raises(ValueError, match="report_batch_item_cannot_be_replayed"):
        ledger.relink_failed_item_for_replay(
            batch_id=batch.batch_id,
            batch_item_id=failed_item.batch_item_id,
            replayed_report_job_id="rjob_replay_2",
            retry_policy=BatchRetryPolicy(max_attempts=3),
        )
    with pytest.raises(ValueError, match="report_batch_item_not_found"):
        ledger.relink_failed_item_for_replay(
            batch_id=batch.batch_id,
            batch_item_id="rbit_missing",
            replayed_report_job_id="rjob_replay_missing",
        )
    with pytest.raises(ValueError, match="report_batch_not_found"):
        ledger.relink_failed_item_for_replay(
            batch_id="rbch_missing",
            batch_item_id="rbit_missing",
            replayed_report_job_id="rjob_replay_missing",
        )


def test_runnable_batch_scan_and_empty_status_refresh_are_bounded(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")

    assert ledger.list_runnable_batch_ids(tenant_ids=["tenant-sg"], limit=0) == []
    with ledger._connect() as connection:
        ledger._refresh_batch_status(connection, "rbch_without_items", now=None)


def test_json_dict_loader_rejects_non_object_payload() -> None:
    assert _json_dict('["not", "an", "object"]') == {}


def test_selected_subset_materializes_selected_items_in_deterministic_order(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    request = BatchCreateRequest(
        selector_mode="selected_subset",
        source_candidates=[
            _candidate("PB_SG_GLOBAL_BAL_003", selected=True),
            _candidate("PB_SG_GLOBAL_BAL_001", selected=True),
            _candidate("PB_SG_GLOBAL_BAL_002", selected=False),
        ],
        as_of_date="2026-04-22",
    )

    batch = ledger.create_batch(
        request=request,
        caller_context=_caller(),
        idempotency_key="batch-selected-subset",
    )

    assert batch.materialized_portfolio_ids == [
        "PB_SG_GLOBAL_BAL_001",
        "PB_SG_GLOBAL_BAL_003",
    ]


def test_all_active_materializes_source_backed_active_candidates_in_deterministic_order(
    tmp_path,
) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    request = BatchCreateRequest(
        selector_mode="all_active_portfolios",
        source_candidates=[
            _candidate("PB_SG_GLOBAL_BAL_003"),
            _candidate("PB_SG_GLOBAL_BAL_001"),
        ],
        as_of_date="2026-04-22",
    )

    batch = ledger.create_batch(
        request=request,
        caller_context=_caller(),
        idempotency_key="batch-all-active",
    )

    assert batch.materialized_portfolio_ids == [
        "PB_SG_GLOBAL_BAL_001",
        "PB_SG_GLOBAL_BAL_003",
    ]


def test_batch_create_is_idempotent_for_same_key_and_same_materialized_request(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    caller = _caller()
    request = _explicit_request("PB_SG_GLOBAL_BAL_001")

    first = ledger.create_batch(
        request=request,
        caller_context=caller,
        idempotency_key="batch-idempotent",
    )
    second = ledger.create_batch(
        request=request,
        caller_context=caller,
        idempotency_key="batch-idempotent",
    )

    assert second == first


def test_batch_create_rejects_same_key_with_incompatible_request(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")
    caller = _caller()
    ledger.create_batch(
        request=_explicit_request("PB_SG_GLOBAL_BAL_001"),
        caller_context=caller,
        idempotency_key="batch-conflict",
    )

    with pytest.raises(BatchIdempotencyConflictError):
        ledger.create_batch(
            request=_explicit_request("PB_SG_GLOBAL_BAL_002"),
            caller_context=caller,
            idempotency_key="batch-conflict",
        )


def test_batch_create_requires_idempotency_key(tmp_path) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")

    with pytest.raises(MissingBatchIdempotencyKeyError):
        ledger.create_batch(
            request=_explicit_request("PB_SG_GLOBAL_BAL_001"),
            caller_context=_caller(),
            idempotency_key=" ",
        )


@pytest.mark.parametrize(
    ("batch_request", "expected_code"),
    [
        (
            BatchCreateRequest(
                selector_mode="explicit_portfolio_list",
                portfolio_ids=[],
                source_candidates=[_candidate("PB_SG_GLOBAL_BAL_001")],
                as_of_date="2026-04-22",
            ),
            "empty_batch_selector",
        ),
        (
            BatchCreateRequest(
                selector_mode="selected_subset",
                source_candidates=[_candidate("PB_SG_GLOBAL_BAL_001", selected=False)],
                as_of_date="2026-04-22",
            ),
            "empty_batch_selector",
        ),
        (
            BatchCreateRequest(
                selector_mode="explicit_portfolio_list",
                portfolio_ids=["PB_SG_GLOBAL_BAL_001"],
                source_candidates=[
                    _candidate("PB_SG_GLOBAL_BAL_001"),
                    _candidate("PB_SG_GLOBAL_BAL_001"),
                ],
                as_of_date="2026-04-22",
            ),
            "duplicate_source_portfolio",
        ),
        (
            BatchCreateRequest(
                selector_mode="explicit_portfolio_list",
                portfolio_ids=["PB_SG_GLOBAL_BAL_001", "PB_SG_GLOBAL_BAL_001"],
                source_candidates=[_candidate("PB_SG_GLOBAL_BAL_001")],
                as_of_date="2026-04-22",
            ),
            "duplicate_requested_portfolio",
        ),
        (
            BatchCreateRequest(
                selector_mode="explicit_portfolio_list",
                portfolio_ids=["PB_SG_GLOBAL_BAL_404"],
                source_candidates=[],
                as_of_date="2026-04-22",
            ),
            "portfolio_not_found",
        ),
        (
            BatchCreateRequest(
                selector_mode="explicit_portfolio_list",
                portfolio_ids=["PB_SG_GLOBAL_BAL_001"],
                source_candidates=[_candidate("PB_SG_GLOBAL_BAL_001", active=False)],
                as_of_date="2026-04-22",
            ),
            "inactive_portfolio",
        ),
        (
            BatchCreateRequest(
                selector_mode="explicit_portfolio_list",
                portfolio_ids=["PB_SG_GLOBAL_BAL_001"],
                source_candidates=[_candidate("PB_SG_GLOBAL_BAL_001", tenant_id="tenant-us")],
                as_of_date="2026-04-22",
            ),
            "portfolio_tenant_mismatch",
        ),
        (
            BatchCreateRequest(
                selector_mode="explicit_portfolio_list",
                portfolio_ids=["PB_SG_GLOBAL_BAL_001"],
                source_candidates=[_candidate("PB_SG_GLOBAL_BAL_001", region="EMEA")],
                as_of_date="2026-04-22",
            ),
            "portfolio_region_mismatch",
        ),
        (
            BatchCreateRequest(
                selector_mode="all_active_portfolios",
                source_candidates=[],
                as_of_date="2026-04-22",
            ),
            "empty_batch_selector",
        ),
        (
            BatchCreateRequest(
                selector_mode="explicit_portfolio_list",
                portfolio_ids=["PB_SG_GLOBAL_BAL_001", "PB_SG_GLOBAL_BAL_002"],
                source_candidates=[
                    _candidate("PB_SG_GLOBAL_BAL_001"),
                    _candidate("PB_SG_GLOBAL_BAL_002"),
                ],
                as_of_date="2026-04-22",
                max_batch_size=1,
            ),
            "batch_size_exceeded",
        ),
    ],
)
def test_selector_validation_rejects_invalid_materialization_cases(
    tmp_path,
    batch_request: BatchCreateRequest,
    expected_code: str,
) -> None:
    ledger = ReportBatchLedger(tmp_path / "batch.sqlite3")

    with pytest.raises(BatchSelectorValidationError) as exc_info:
        ledger.create_batch(
            request=batch_request,
            caller_context=_caller(),
            idempotency_key=f"batch-invalid-{expected_code}",
        )

    assert exc_info.value.code == expected_code


def test_runnable_scan_round_robins_across_tenants(tmp_path):
    """Issue #178 review: one backlogged tenant must not monopolize the bounded
    scan window. Every tenant's oldest batch outranks any tenant's second-oldest."""

    ledger = ReportBatchLedger(tmp_path / "fairness.sqlite3")

    def _create(tenant: str, index: int):
        caller = _caller().model_copy(
            update={"tenant_id": tenant, "correlation_id": f"corr-{tenant}-{index}"}
        )
        request = _explicit_request(f"PB_{index}")
        request = request.model_copy(
            update={
                "source_candidates": [
                    candidate.model_copy(update={"tenant_id": tenant})
                    for candidate in request.source_candidates
                ]
            }
        )
        return ledger.create_batch(
            request=request,
            caller_context=caller,
            idempotency_key=f"fair-{tenant}-{index}",
        )

    backlogged = [_create("tenant-busy", index) for index in range(3)]
    quiet = _create("tenant-quiet", 9)

    scanned = ledger.list_runnable_batch_ids(tenant_ids=["tenant-busy", "tenant-quiet"], limit=2)

    # A pure created_at order would return the busy tenant's two oldest and
    # starve the quiet tenant; round-robin returns each tenant's oldest first.
    assert backlogged[0].batch_id in scanned
    assert quiet.batch_id in scanned
