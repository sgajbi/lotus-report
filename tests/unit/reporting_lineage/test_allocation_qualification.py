import copy
import hashlib
import json
import time

import pytest
from pydantic import ValidationError

from app.application_errors import ReportingUpstreamError
from app.models.allocation_qualification import AllocationQualification, AllocationValuationCoverage
from app.reporting_lineage.allocation_qualification import (
    allocation_items,
    allocation_requested,
    map_source_allocation,
    snapshot_allocation,
    top_allocation_bucket,
)
from app.reporting_lineage.capture_service import _UpstreamRecorder
from app.services.reporting_read_service import ReportingReadService

REQUEST = {"scope": {"portfolio_id": "P1"}, "as_of_date": "2026-04-10", "reporting_currency": "USD"}


def source_allocation():
    return {
        "scope_type": "portfolio",
        "scope": {"portfolio_id": "P1"},
        "resolved_as_of_date": "2026-04-10",
        "reporting_currency": "USD",
        "total_market_value_reporting_currency": None,
        "valuation_coverage": {
            "coverage_state": "PARTIAL",
            "coverage_reason": "market_value_missing",
            "snapshot_row_count": 2,
            "expected_open_position_count": 2,
            "valued_position_count": 1,
            "unvalued_position_count": 1,
        },
        "views": [
            {
                "dimension": "asset_class",
                "total_market_value_reporting_currency": None,
                "buckets": [
                    {
                        "dimension_value": "Equity",
                        "weight": None,
                        "market_value_reporting_currency": "120",
                        "position_count": 1,
                    },
                    {
                        "dimension_value": "Cash",
                        "weight": None,
                        "market_value_reporting_currency": None,
                        "position_count": 1,
                    },
                ],
            }
        ],
    }


class Core:
    def __init__(self, allocation):
        self.allocation = allocation

    async def get_portfolio_summary(self, **kwargs):
        assert kwargs["admitted_tenant_id"] == "tenant-a"
        return 200, {
            "portfolio_id": "P1",
            "totals": {},
            "snapshot_metadata": {},
            "reporting_currency": "USD",
        }

    async def get_asset_allocation(self, **kwargs):
        assert kwargs["admitted_tenant_id"] == "tenant-a"
        return 200, self.allocation


@pytest.mark.asyncio
async def test_public_summary_preserves_independent_unknown_weight_and_value():
    source = source_allocation()
    before = copy.deepcopy(source)
    service = ReportingReadService(
        core_query_client=Core(source), performance_client=object(), risk_client=object()
    )
    result = await service.get_portfolio_summary(
        "P1",
        {"as_of_date": "2026-04-10", "sections": ["ALLOCATION"]},
        "correlation",
        admitted_tenant_id="tenant-a",
    )
    rows = result["allocation"]["byAssetClass"]
    assert rows[0]["weight"] is None
    assert rows[0]["market_value"] == 120
    assert rows[1]["weight"] is None and rows[1]["market_value"] is None
    assert source == before


def covered_source(values=(120, -20), weights=(1.2, -0.2), state="COMPLETE"):
    source = source_allocation()
    source["total_market_value_reporting_currency"] = sum(values)
    source["views"][0]["total_market_value_reporting_currency"] = sum(values)
    source["valuation_coverage"].update(
        coverage_state=state,
        coverage_reason={
            "COMPLETE": "all_source_positions_covered",
            "MEASURED_ZERO": "source_measured_zero",
            "CARRY_FORWARD": "latest_source_snapshot_precedes_as_of_date",
        }[state],
        valued_position_count=2,
        unvalued_position_count=0,
    )
    for row, amount, weight in zip(source["views"][0]["buckets"], values, weights, strict=True):
        row.update(market_value_reporting_currency=amount, weight=weight)
    return source


@pytest.mark.parametrize(
    "values,weights,state,publication",
    [
        ((80, 20), (0.8, 0.2), "COMPLETE", True),
        ((120, -20), (1.2, -0.2), "COMPLETE", True),
        ((100, 0), (1, 0), "COMPLETE", True),
        ((0, 0), (0, 0), "MEASURED_ZERO", True),
        ((0, 0), (None, None), "MEASURED_ZERO", False),
        ((120, -20), (1.2, -0.2), "CARRY_FORWARD", False),
    ],
)
def test_source_figures_remain_exact_and_coverage_is_not_recomputed(
    values, weights, state, publication
):
    source = covered_source(values, weights, state)
    source["look_through"] = {"applied_mode": "look_through", "decomposed_position_count": 2}
    source["calculation_lineage"] = {
        "source_data_hash": "sha256:source",
        "policy_version": "core-v1",
    }
    source["views"][0]["buckets"][1]["contributors"] = [
        {"security_id": "CASH", "market_value_reporting_currency": values[1], "weight": weights[1]}
    ]
    before = copy.deepcopy(source)
    mapped = map_source_allocation(source, REQUEST)
    assert [row["market_value"] for row in mapped["byAssetClass"]] == list(values)
    assert [row["weight"] for row in mapped["byAssetClass"]] == list(weights)
    assert mapped["valuation_coverage"] == source["valuation_coverage"]
    assert mapped["qualification"]["client_publication_allowed"] is publication
    assert mapped["look_through"] == source["look_through"]
    assert mapped["calculation_lineage"] == source["calculation_lineage"]
    assert source == before


@pytest.mark.parametrize(
    "coverage_update",
    [
        {"coverage_state": "COMPLETE"},
        {"coverage_reason": "arbitrary untrusted narrative"},
        {"snapshot_row_count": -1},
        {"valued_position_count": True},
        {"unvalued_position_count": 0},
        {"expected_open_position_count": 3, "coverage_reason": "all_source_positions_covered"},
    ],
)
def test_invalid_coverage_is_bounded_and_cannot_promote_capture(coverage_update):
    source = source_allocation()
    source["valuation_coverage"].update(coverage_update)
    mapped = map_source_allocation(source, REQUEST)
    assert mapped["qualification"]["status"] == "invalid"
    assert mapped["qualification"]["coverage"] is None
    assert mapped["qualification"]["client_publication_allowed"] is False
    assert mapped["supportability"]["status"] == "partial"


@pytest.mark.parametrize(
    "bad", [True, "NaN", "Infinity", "-Infinity", "malformed", "1e1000", "1e-1000"]
)
@pytest.mark.parametrize("field", ["weight", "market_value_reporting_currency"])
def test_nonfinite_or_malformed_source_numbers_never_become_zero(bad, field):
    source = covered_source()
    source["views"][0]["buckets"][1][field] = bad
    mapped = map_source_allocation(source, REQUEST)
    key = "market_value" if field == "market_value_reporting_currency" else field
    assert mapped["byAssetClass"][1][key] is None
    assert mapped["qualification"]["status"] == "invalid"
    assert mapped["qualification"]["client_publication_allowed"] is False
    json.dumps(mapped, allow_nan=False)


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", {"portfolio_id": "FOREIGN"}),
        ("scope", {"portfolio_ids": ["P1", "FOREIGN"]}),
        ("scope_type", "multi_portfolio"),
        ("resolved_as_of_date", "2026-04-09"),
        ("reporting_currency", "EUR"),
        ("reporting_currency", None),
        ("scope", {}),
    ],
)
def test_source_binding_refuses_unresolved_or_foreign_figures(field, value):
    source = source_allocation()
    source[field] = value
    with pytest.raises(ReportingUpstreamError, match="allocation source"):
        map_source_allocation(source, REQUEST)


def test_partial_values_and_weights_do_not_rank_against_unknown_competitors():
    mapped = map_source_allocation(source_allocation(), REQUEST)
    assert mapped["valuation_coverage"]["unvalued_position_count"] == 1
    assert top_allocation_bucket(mapped["byAssetClass"]) is None
    items = allocation_items({**mapped, "irrelevant": [{"group": "fake", "weight": 100}]})
    assert len(items) == 2 and items[1]["group"] == "Cash"
    assert items[0]["weight"] is None and items[0]["market_value"] == 120
    assert items[1]["weight"] is None and items[1]["market_value"] is None
    rows = [{"group": "Short", "weight": -0.2}, {"group": "Known zero", "weight": 0}]
    assert top_allocation_bucket(rows)["group"] == "Known zero"
    rows.append({"group": "Unknown", "weight": None})
    assert top_allocation_bucket(rows) is None


@pytest.mark.parametrize("source", [source_allocation(), covered_source()])
def test_native_recorder_uses_qualified_source_success(source):
    recorder = _UpstreamRecorder(correlation_id="allocation-corr", trace_id="allocation-trace")
    recorder.append_success(
        service_name="lotus-core",
        endpoint="/reporting/asset-allocation/query",
        method="POST",
        request_payload=REQUEST,
        status_code=200,
        response_payload=source,
        started_at=time.perf_counter(),
    )
    expected = (
        "partial" if source["valuation_coverage"]["coverage_state"] == "PARTIAL" else "complete"
    )
    assert recorder.calls[0].supportability_status == expected
    assert recorder.calls[0].completeness_status == expected
    assert recorder.calls[0].response_payload == source


@pytest.mark.parametrize("legacy", [True, False])
def test_snapshot_presentation_cannot_rewrite_hash_or_attest_a_legacy_zero(legacy):
    mapped = map_source_allocation(covered_source((100, 0), (1, 0)), REQUEST)
    if legacy:
        mapped.pop("qualification")
        mapped.pop("valuation_coverage")
    snapshot = {
        "portfolio_id": "P1",
        "as_of_date": "2026-04-10",
        "reportingCurrency": "USD",
        "allocation": mapped,
    }
    original = json.dumps(snapshot, sort_keys=True).encode()
    digest = hashlib.sha256(original).hexdigest()
    presented = snapshot_allocation(snapshot)
    assert json.dumps(snapshot, sort_keys=True).encode() == original
    assert hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest() == digest
    zero = presented["byAssetClass"][1]
    assert zero["group"] == "Cash" and zero["position_count"] == 1
    assert zero["weight"] is (None if legacy else zero["weight"])
    assert zero["market_value"] == (None if legacy else 0)
    assert presented["qualification"]["client_publication_allowed"] is (not legacy)


def test_unbound_retained_qualification_and_forged_complete_model_are_refused():
    allocation = map_source_allocation(covered_source(), REQUEST)
    snapshot = {
        "portfolio_id": "FOREIGN",
        "as_of_date": "2026-04-10",
        "reportingCurrency": "USD",
        "allocation": allocation,
    }
    assert snapshot_allocation(snapshot)["byAssetClass"][0]["market_value"] is None
    with pytest.raises(ValidationError):
        AllocationQualification(
            status="complete",
            reason_code="allocation_valuation_complete",
            client_publication_allowed=True,
        )


def test_contributor_null_residual_and_foreign_scope_are_not_laundered():
    source = source_allocation()
    bucket = source["views"][0]["buckets"][1]
    bucket.update(
        contributor_count=1,
        omitted_contributor_count=0,
        omitted_market_value_reporting_currency=None,
        contributors=[
            {
                "portfolio_id": "P1",
                "security_id": "CASH",
                "market_value_reporting_currency": None,
                "bucket_weight": None,
                "component_weight": None,
            }
        ],
    )
    mapped = map_source_allocation(source, REQUEST)["byAssetClass"][1]
    assert mapped["contributors"] == bucket["contributors"]
    assert mapped["omitted_market_value_reporting_currency"] is None
    bucket["contributors"][0]["portfolio_id"] = "FOREIGN"
    with pytest.raises(ReportingUpstreamError, match="contributor scope"):
        map_source_allocation(source, REQUEST)


def test_malformed_contributor_money_is_unavailable_and_prevents_complete_posture():
    source = covered_source()
    source["views"][0]["buckets"][0]["contributors"] = [
        {"portfolio_id": "P1", "market_value_reporting_currency": "NaN", "bucket_weight": "1"}
    ]
    mapped = map_source_allocation(source, REQUEST)
    assert mapped["byAssetClass"][0]["contributors"][0]["market_value_reporting_currency"] is None
    assert mapped["qualification"]["status"] == "invalid"
    assert mapped["qualification"]["client_publication_allowed"] is False


@pytest.mark.parametrize(
    "change",
    [
        {"status": "partial", "reason_code": "allocation_valuation_complete"},
        {"source_as_of_date": "2026-02-30"},
        {"client_publication_allowed": "false"},
    ],
)
def test_incoherent_retained_attestation_suppresses_figures(change):
    allocation = map_source_allocation(covered_source(), REQUEST)
    allocation["qualification"].update(change)
    snapshot = {
        "portfolio_id": "P1",
        "as_of_date": "2026-04-10",
        "reportingCurrency": "USD",
        "allocation": allocation,
    }
    presented = snapshot_allocation(snapshot)
    assert presented["qualification"]["status"] == "invalid"
    assert presented["qualification"]["client_publication_allowed"] is False
    assert presented["byAssetClass"][0]["market_value"] is None


@pytest.mark.parametrize("case", ["empty_complete_view", "populated_loaded_empty"])
def test_each_source_view_must_match_the_coverage_emptiness(case):
    source = covered_source()
    if case == "empty_complete_view":
        source["views"].append(
            {"dimension": "currency", "buckets": [], "total_market_value_reporting_currency": 100}
        )
    else:
        source = covered_source((0, 0), (0, 0), "MEASURED_ZERO")
        source["valuation_coverage"].update(
            coverage_state="LOADED_EMPTY",
            coverage_reason="source_snapshot_has_no_open_positions",
            snapshot_row_count=0,
            expected_open_position_count=0,
            valued_position_count=0,
        )
    mapped = map_source_allocation(source, REQUEST)
    assert mapped["qualification"]["status"] == "invalid"
    assert mapped["qualification"]["client_publication_allowed"] is False
    recorder = _UpstreamRecorder(correlation_id="corr", trace_id="trace")
    recorder.append_success(
        service_name="lotus-core",
        endpoint="/reporting/asset-allocation/query",
        method="POST",
        request_payload=REQUEST,
        status_code=200,
        response_payload=source,
        started_at=time.perf_counter(),
    )
    assert recorder.calls[0].completeness_status == "partial"
    # A forged complete retained attestation cannot bypass structural admission.
    retained = map_source_allocation(covered_source(), REQUEST)
    if case == "empty_complete_view":
        retained["byCurrency"] = []
        retained["view_totals"]["byCurrency"] = 100
    else:
        retained["valuation_coverage"] = source["valuation_coverage"]
        retained["qualification"]["coverage"] = source["valuation_coverage"]
    presented = snapshot_allocation(
        {
            "portfolio_id": "P1",
            "as_of_date": "2026-04-10",
            "reportingCurrency": "USD",
            "allocation": retained,
        }
    )
    assert presented["qualification"]["status"] == "invalid"
    assert presented["qualification"]["client_publication_allowed"] is False


@pytest.mark.parametrize(
    "field", ["position_count", "contributor_count", "omitted_contributor_count"]
)
@pytest.mark.parametrize("bad", [True, -1, "malformed"])
def test_bad_bucket_counts_are_not_publication_evidence(field, bad):
    source = covered_source()
    source["views"][0]["buckets"][0][field] = bad
    mapped = map_source_allocation(source, REQUEST)
    assert mapped["byAssetClass"][0][field] is None
    assert mapped["qualification"]["status"] == "invalid"
    assert mapped["qualification"]["client_publication_allowed"] is False
    retained = map_source_allocation(covered_source(), REQUEST)
    retained["byAssetClass"][0][field] = bad
    presented = snapshot_allocation(
        {
            "portfolio_id": "P1",
            "as_of_date": "2026-04-10",
            "reportingCurrency": "USD",
            "allocation": retained,
        }
    )
    assert presented["byAssetClass"][0][field] is None
    assert presented["qualification"]["status"] == "invalid"
    assert presented["qualification"]["client_publication_allowed"] is False


@pytest.mark.parametrize("total,omitted,expected", [(2, 1, True), (2, 0, False), (0, None, False)])
def test_contributor_count_is_bound_to_displayed_and_omitted_rows(total, omitted, expected):
    source = covered_source()
    row = source["views"][0]["buckets"][0]
    # Expanded contributor counts are intentionally distinct from source position count.
    row.update(
        contributors=[{"security_id": "EQ", "market_value_reporting_currency": 120}],
        contributor_count=total,
    )
    if omitted is not None:
        row["omitted_contributor_count"] = omitted
    mapped = map_source_allocation(source, REQUEST)
    assert mapped["qualification"]["client_publication_allowed"] is expected
    assert mapped["byAssetClass"][0]["position_count"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"snapshot_row_count": 0, "expected_open_position_count": 0, "valued_position_count": 0},
        {"expected_open_position_count": 3},
        {
            "coverage_state": "LOADED_EMPTY",
            "coverage_reason": "source_snapshot_has_no_open_positions",
        },
        {"coverage_state": "UNAVAILABLE", "coverage_reason": "no_source_snapshot"},
        {"coverage_state": "PARTIAL", "coverage_reason": "market_value_missing"},
        {"coverage_state": "PARTIAL", "coverage_reason": "open_position_coverage_gap"},
    ],
)
def test_source_coverage_cannot_contradict_its_counts(change):
    coverage = covered_source()["valuation_coverage"]
    coverage.update(change)
    with pytest.raises(ValidationError):
        AllocationValuationCoverage.model_validate(coverage)


def test_valid_unavailable_gap_is_distinct_from_missing_or_unvalued_observations():
    coverage = {
        "coverage_state": "UNAVAILABLE",
        "coverage_reason": "open_position_coverage_gap",
        "snapshot_row_count": 0,
        "expected_open_position_count": 2,
        "valued_position_count": 0,
        "unvalued_position_count": 0,
    }
    assert AllocationValuationCoverage.model_validate(coverage).coverage_state == "UNAVAILABLE"


def test_complete_publication_requires_all_three_source_bindings():
    with pytest.raises(ValidationError, match="unbound allocation publication"):
        AllocationQualification(
            status="complete",
            reason_code="allocation_valuation_complete",
            coverage=covered_source()["valuation_coverage"],
            client_publication_allowed=True,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("buckets", [{"dimension_value": " "}]),
        ("contributors", "malformed"),
        ("contributors", ["malformed"]),
        ("omitted_market_value_reporting_currency", "Infinity"),
    ],
)
def test_malformed_source_bucket_provenance_prevents_publication(field, value):
    source = covered_source()
    if field == "buckets":
        source["views"][0][field] = value
    else:
        source["views"][0]["buckets"][0][field] = value
    mapped = map_source_allocation(source, REQUEST)
    assert mapped["qualification"]["status"] == "invalid"
    assert mapped["qualification"]["client_publication_allowed"] is False
    if field == "omitted_market_value_reporting_currency":
        assert mapped["byAssetClass"][0][field] is None


@pytest.mark.parametrize("rows", ["malformed", ["malformed"]])
def test_retained_malformed_dimensions_prevent_publication(rows):
    retained = map_source_allocation(covered_source(), REQUEST)
    retained["byAssetClass"] = rows
    presented = snapshot_allocation(
        {
            "portfolio_id": "P1",
            "as_of_date": "2026-04-10",
            "reportingCurrency": "USD",
            "allocation": retained,
        }
    )
    assert presented["qualification"]["status"] == "invalid"
    assert presented["qualification"]["client_publication_allowed"] is False


def test_retained_missing_weight_prevents_complete_readiness_even_with_known_value():
    retained = map_source_allocation(covered_source(), REQUEST)
    retained["byAssetClass"][0]["weight"] = None
    presented = snapshot_allocation(
        {
            "portfolio_id": "P1",
            "as_of_date": "2026-04-10",
            "reportingCurrency": "USD",
            "allocation": retained,
        }
    )
    assert presented["byAssetClass"][0]["market_value"] == 120
    assert presented["qualification"]["reason_code"] == "allocation_numeric_unavailable"
    assert presented["qualification"]["client_publication_allowed"] is False


def test_malformed_ranking_row_cannot_become_largest_bucket():
    assert top_allocation_bucket([{"group": "Equity", "weight": 1}, "malformed"]) is None


def test_invalid_calendar_source_binding_is_refused_before_projection():
    source = covered_source()
    source["resolved_as_of_date"] = "2026-02-30"
    with pytest.raises(ReportingUpstreamError, match="binding missing"):
        map_source_allocation(source, {**REQUEST, "as_of_date": "2026-02-30"})


@pytest.mark.parametrize("totals", [None, [], "malformed"])
@pytest.mark.parametrize("legacy", [False, True])
def test_retained_malformed_totals_container_is_bounded_and_immutable(totals, legacy):
    retained = map_source_allocation(covered_source(), REQUEST)
    retained["view_totals"] = totals
    if legacy:
        retained.pop("qualification")
        retained.pop("valuation_coverage")
    original = copy.deepcopy(retained)
    presented = snapshot_allocation(
        {
            "portfolio_id": "P1",
            "as_of_date": "2026-04-10",
            "reportingCurrency": "USD",
            "allocation": retained,
        }
    )
    assert retained == original
    assert presented["qualification"]["status"] == ("missing" if legacy else "invalid")
    assert presented["qualification"]["client_publication_allowed"] is False


@pytest.mark.parametrize(
    "options,requested",
    [
        ({}, True),
        ({"sections": None}, True),
        ({"sections": "ALLOCATION"}, True),
        ({"sections": []}, True),
        ({"sections": [None]}, True),
        ({"sections": ["allocation"]}, True),
        ({"sections": ["OVERVIEW"]}, False),
    ],
)
def test_allocation_request_uses_the_captured_order_options(options, requested):
    assert allocation_requested(options) is requested


@pytest.mark.parametrize(
    "raw,status", [(None, "missing"), ({}, "missing"), ([], "invalid"), ("bad", "invalid")]
)
def test_requested_allocation_container_cannot_silently_disappear(raw, status):
    snapshot = {"allocation": raw}
    before = copy.deepcopy(snapshot)
    presented = snapshot_allocation(snapshot, requested=True)
    assert presented["qualification"]["status"] == status
    assert presented["qualification"]["client_publication_allowed"] is False
    assert snapshot == before


def test_absent_requested_allocation_is_missing_but_an_omitted_section_is_neutral():
    assert snapshot_allocation({}, requested=True)["qualification"]["status"] == "missing"
    assert snapshot_allocation({}, requested=False) == {}


@pytest.mark.parametrize(
    "field,value",
    [
        ("group", []),
        ("group", " "),
        ("contributors", None),
        ("contributors", "bad"),
        ("contributors", ["bad"]),
        ("contributors", [{"portfolio_id": "foreign"}]),
        ("contributors", [{"portfolio_id": "P1", "market_value_reporting_currency": True}]),
        ("contributors", [{"portfolio_id": "P1", "bucket_weight": "NaN"}]),
        ("contributors", [{"portfolio_id": "P1", "component_weight": "Infinity"}]),
        ("omitted_market_value_reporting_currency", "NaN"),
    ],
)
@pytest.mark.parametrize("legacy", [False, True])
def test_retained_row_metadata_cannot_bypass_qualification(field, value, legacy):
    retained = map_source_allocation(covered_source(), REQUEST)
    retained["byAssetClass"][0][field] = value
    if legacy:
        retained.pop("qualification")
        retained.pop("valuation_coverage")
    snapshot = {
        "portfolio_id": "P1",
        "as_of_date": "2026-04-10",
        "reportingCurrency": "USD",
        "allocation": retained,
    }
    before = copy.deepcopy(snapshot)
    presented = snapshot_allocation(snapshot)
    assert presented["qualification"]["status"] == ("missing" if legacy else "invalid")
    assert presented["qualification"]["client_publication_allowed"] is False
    assert snapshot == before
    if field == "group":
        assert len(presented["byAssetClass"]) == 1
    elif field == "contributors" and value == [{"portfolio_id": "foreign"}]:
        assert presented["byAssetClass"][0]["contributors"] == []
        assert presented["byAssetClass"][0]["market_value"] is None


@pytest.mark.parametrize("omitted", [None, 2])
def test_valid_retained_truncated_contributors_and_expanded_counts_remain_complete(omitted):
    retained = map_source_allocation(covered_source(), REQUEST)
    row = retained["byAssetClass"][0]
    row.update(
        position_count=7,
        contributor_count=3,
        contributors_truncated=True,
        contributors=[
            {
                "portfolio_id": "P1",
                "market_value_reporting_currency": "120",
                "bucket_weight": 1.2,
                "component_weight": 1,
            }
        ],
        omitted_market_value_reporting_currency=0,
    )
    if omitted is not None:
        row["omitted_contributor_count"] = omitted
    snapshot = {
        "portfolio_id": "P1",
        "as_of_date": "2026-04-10",
        "reportingCurrency": "USD",
        "allocation": retained,
    }
    before = copy.deepcopy(snapshot)
    presented = snapshot_allocation(snapshot)
    assert presented["qualification"]["client_publication_allowed"] is True
    assert presented["byAssetClass"][0] == row
    assert snapshot == before


@pytest.mark.parametrize(
    "view",
    [
        None,
        {"dimension": []},
        {"dimension": "unknown"},
        {"dimension": "asset_class", "buckets": None},
    ],
)
def test_malformed_source_views_cannot_become_complete(view):
    source = covered_source()
    source["views"].append(view)
    result = map_source_allocation(source, REQUEST)
    assert result["qualification"]["status"] == "invalid"
    assert all(row["weight"] is None for row in result["byAssetClass"])


def test_legacy_source_without_coverage_remains_missing_and_bad_date_refuses_binding():
    source = covered_source()
    source.pop("valuation_coverage")
    assert map_source_allocation(source, REQUEST)["qualification"]["status"] == "missing"
    source["resolved_as_of_date"] = None
    assert (
        map_source_allocation(source, REQUEST)["qualification"]["client_publication_allowed"]
        is False
    )
    source = covered_source()
    source["resolved_as_of_date"] = None
    with pytest.raises(ReportingUpstreamError, match="binding missing"):
        map_source_allocation(source, REQUEST)
