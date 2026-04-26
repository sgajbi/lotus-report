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
                source_candidates=[_candidate("PB_SG_GLOBAL_BAL_001")],
                as_of_date="2026-04-22",
            ),
            "unsupported_batch_selector",
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
