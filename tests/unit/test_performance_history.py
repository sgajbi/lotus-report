"""Admission and retained-presentation controls for source-owned history statements."""

import copy
from time import perf_counter

import pytest

from app.reporting_lineage.capture_service import _hash_payload, _UpstreamRecorder
from app.reporting_render.package_builder import (
    _governance_summary_section,
    _review_observations,
    _summary_paragraph,
)
from app.reporting_render.performance_history import snapshot_history_qualification
from app.services.performance_history import qualify_performance_history


def source_facts():
    return {
        "portfolio_id": "HISTORY_OWNER",
        "calculation_id": "10000000-0000-4000-8000-000000000001",
        "input_mode": "stateful",
        "results_by_period": {
            "YTD": {
                "portfolio_twr": {
                    "net": {
                        "summary": {"cumulative_return": {"base": 0.0}},
                    }
                }
            }
        },
        "calculation_supportability": {
            "state": "ready",
            "reason": "calculation_complete",
            "freshness_bucket": "current",
            "history_coverage": {
                "status": "complete",
                "calculation_basis": "requested_window",
                "requested_start_date": "2026-01-01",
                "requested_end_date": "2026-01-09",
                "covered_start_date": "2025-12-31",
                "covered_end_date": "2026-01-10",
                "effective_start_date": "2026-01-02",
                "effective_end_date": "2026-01-09",
                "calendar_basis": "natural_days",
                "missing_required_observation_count": 0,
                "missing_required_observation_dates_sample": [],
                "reason_codes": [
                    "covered_window_matches_requested_window",
                    "beginning_market_value_baseline_applied",
                    "explicit_ignored_dates_applied",
                ],
            },
        },
    }


def source_request():
    return {
        "portfolio_id": "HISTORY_OWNER",
        "report_end_date": "2026-01-09",
        "periods": [{"period": "YTD"}],
        "calendar": {"type": "NATURAL"},
    }


def qualify(payload):
    return qualify_performance_history(
        payload,
        portfolio_id="HISTORY_OWNER",
        as_of_date="2026-01-09",
        source_request=source_request(),
    )


def test_complete_source_baseline_exclusions_and_outside_observations_are_admitted():
    payload = source_facts()
    result = qualify(payload)
    assert result["client_publication_allowed"] is True
    assert result["coverage"] == payload["calculation_supportability"]["history_coverage"]
    assert result["period_return_bases"] == {"YTD": ["NET_TWR"]}


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "5.0", None])
def test_source_non_numeric_or_non_finite_return_never_attests_publication(value):
    payload = source_facts()
    payload["results_by_period"]["YTD"]["portfolio_twr"]["net"]["summary"]["cumulative_return"][
        "base"
    ] = value
    assert qualify(payload)["client_publication_allowed"] is False


@pytest.mark.parametrize("value", [0, -5, 5.25])
def test_source_finite_return_admission_preserves_qualification(value):
    payload = source_facts()
    payload["results_by_period"]["YTD"]["portfolio_twr"]["net"]["summary"]["cumulative_return"][
        "base"
    ] = value
    assert qualify(payload)["client_publication_allowed"] is True


@pytest.mark.parametrize(
    "case",
    [
        "mode",
        "period",
        "long-period",
        "too-many-periods",
        "request-period",
        "too-many-request-periods",
    ],
)
def test_unsupported_history_metadata_cannot_leak_or_attest_filtered_source(case):
    payload, request = source_facts(), source_request()
    periods = payload["results_by_period"]
    if case == "mode":
        payload["input_mode"] = "untrusted-source-detail"
    elif case == "period":
        periods["untrusted-source-detail"] = copy.deepcopy(periods["YTD"])
    elif case == "long-period":
        periods["untrusted-source-detail" * 100] = copy.deepcopy(periods["YTD"])
    elif case == "too-many-periods":
        periods.update({f"untrusted-source-detail-{i}": periods["YTD"] for i in range(15)})
    elif case == "request-period":
        request["periods"].append({"period": "untrusted-source-detail"})
    else:
        request["periods"] = [{"period": "YTD"}] * 15
    before = copy.deepcopy(payload)
    result = qualify_performance_history(
        payload, portfolio_id="HISTORY_OWNER", as_of_date="2026-01-09", source_request=request
    )
    assert "untrusted-source-detail" not in str(result)
    assert result["status"] == "invalid" and result["client_publication_allowed"] is False
    assert result["reason_code"] == "performance_history_qualification_invalid"
    assert len(result["returned_periods"]) <= 14 and len(result["requested_periods"]) <= 14
    assert payload == before


def test_all_recognized_workspace_periods_remain_valid_at_the_admission_budget():
    codes = [
        "1D",
        "2D",
        "5D",
        "10D",
        "1M",
        "3M",
        "6M",
        "YTD",
        "1Y",
        "2Y",
        "5Y",
        "10Y",
        "SI",
        "EXPLICIT",
    ]
    payload, request = source_facts(), source_request()
    row = payload["results_by_period"]["YTD"]
    payload["results_by_period"] = {code: copy.deepcopy(row) for code in codes}
    request["periods"] = [{"period": code} for code in codes]
    result = qualify_performance_history(
        payload, portfolio_id="HISTORY_OWNER", as_of_date="2026-01-09", source_request=request
    )
    assert result["client_publication_allowed"] is True
    assert result["returned_periods"] == result["requested_periods"] == codes


@pytest.mark.parametrize("coverage_present,expected", [(False, "missing"), (True, "mismatched")])
def test_absent_input_mode_preserves_legacy_missing_history_without_attestation(
    coverage_present, expected
):
    payload = source_facts()
    payload.pop("input_mode")
    if not coverage_present:
        payload["calculation_supportability"].pop("history_coverage")
    result = qualify(payload)
    assert result["status"] == expected
    assert result["source_input_mode"] is None
    assert result["client_publication_allowed"] is False


@pytest.mark.parametrize(
    "case",
    [
        "complete_gap_reason",
        "complete_no_observations",
        "duplicates",
        "unbounded_reasons",
        "negative_count",
        "boolean_count",
        "bad_sample_order",
        "outside_sample",
        "unpaired_bounds",
        "outside_effective",
        "reverse_requested",
        "wrong_calendar",
        "unexpected_period",
        "empty_summary",
        "malformed_summary",
        "missing_net_basis",
        "missing_requested_period",
        "no_supportability",
        "unknown_source_state",
    ],
)
def test_malformed_or_incompatible_attestations_never_authorize_publication(case):
    payload = source_facts()
    support = payload["calculation_supportability"]
    coverage = support["history_coverage"]
    if case == "complete_gap_reason":
        coverage["reason_codes"].append("leading_history_missing")
    elif case == "complete_no_observations":
        coverage["reason_codes"] = ["no_observations_in_requested_window"]
    elif case == "duplicates":
        coverage["reason_codes"].append(coverage["reason_codes"][0])
    elif case == "unbounded_reasons":
        coverage["reason_codes"] *= 10
    elif case == "negative_count":
        coverage["missing_required_observation_count"] = -1
    elif case == "boolean_count":
        coverage["missing_required_observation_count"] = True
    elif case == "bad_sample_order":
        coverage.update(
            status="partial",
            calculation_basis="available_window",
            reason_codes=["interior_history_missing"],
            missing_required_observation_count=2,
            missing_required_observation_dates_sample=["2026-01-06", "2026-01-05"],
        )
    elif case == "outside_sample":
        coverage.update(
            status="partial",
            calculation_basis="available_window",
            reason_codes=["leading_history_missing"],
            missing_required_observation_count=1,
            missing_required_observation_dates_sample=["2025-12-31"],
        )
    elif case == "unpaired_bounds":
        coverage["covered_start_date"] = None
    elif case == "outside_effective":
        coverage["effective_start_date"] = "2025-12-31"
    elif case == "reverse_requested":
        coverage["requested_start_date"] = "2026-01-10"
    elif case == "wrong_calendar":
        coverage["calendar_basis"] = "business_weekdays"
    elif case == "unexpected_period":
        payload["results_by_period"]["UNREQUESTED"] = payload["results_by_period"].pop("YTD")
    elif case in {"empty_summary", "malformed_summary"}:
        payload["results_by_period"]["YTD"]["portfolio_twr"]["net"]["summary"] = (
            {} if case == "empty_summary" else {"cumulative_return": "garbage"}
        )
    elif case == "missing_net_basis":
        twr = payload["results_by_period"]["YTD"]["portfolio_twr"]
        twr["gross"] = twr.pop("net")
    elif case == "missing_requested_period":
        payload["results_by_period"] = {}
    elif case == "no_supportability":
        payload.pop("calculation_supportability")
    else:
        support["state"] = "future_unrecognized_state"
    assert qualify(payload)["client_publication_allowed"] is False


@pytest.mark.parametrize("history_status", ["complete", "partial", "unknown", "missing"])
def test_recorder_classifies_workspace_history_semantically(history_status):
    payload = source_facts()
    if history_status == "missing":
        payload["calculation_supportability"].pop("history_coverage")
    elif history_status != "complete":
        coverage = payload["calculation_supportability"]["history_coverage"]
        coverage.update(
            status=history_status,
            calculation_basis="available_window",
            calendar_basis="business_weekdays" if history_status == "unknown" else "natural_days",
            missing_required_observation_count=1,
            missing_required_observation_dates_sample=["2026-01-06"],
            reason_codes=["interior_history_missing"]
            + (["venue_calendar_not_attested"] if history_status == "unknown" else []),
        )
    request = source_request()
    if history_status == "unknown":
        request["calendar"] = {"type": "BUSINESS", "trading_calendar": "SOURCE_NAMED_CALENDAR"}
    recorder = _UpstreamRecorder(correlation_id="history-corr", trace_id="history-trace")
    recorder.append_success(
        service_name="lotus-performance",
        endpoint="/performance/workspace-summary",
        method="POST",
        request_payload=request,
        status_code=200,
        response_payload=payload,
        started_at=perf_counter(),
    )
    call = recorder.calls[0]
    assert call.supportability_status == ("complete" if history_status == "complete" else "partial")
    assert call.completeness_status == call.supportability_status
    assert call.response_payload == payload
    assert call.failure_category == ("none" if history_status == "complete" else "partial_data")


def test_legacy_render_qualifies_governance_and_notes_without_mutating_snapshot():
    snapshot = {
        "portfolio_id": "HISTORY_OWNER",
        "as_of_date": "2026-01-09",
        "performance": {"summary": {"YTD": {"net_cumulative_return": 0.0}}},
        "readiness": {"status": "ready"},
        "reviewObservations": [{"summary": "Existing reviewed observation."}],
    }
    before = copy.deepcopy(snapshot)
    original_hash = _hash_payload(snapshot)
    notes = _review_observations(snapshot, {}, {}, {})
    assert notes[0] == "Existing reviewed observation."
    assert "qualification: missing" in notes[-1]
    summary = _governance_summary_section(
        snapshot=snapshot,
        evidence={},
        trust_metadata={
            "completeness_status": "complete",
            "data_quality_status": "quality_passed",
        },
    )
    assert summary["completeness_status"] == "partial"
    assert summary["data_quality_status"] == "quality_warning"
    assert summary["readiness_status"] == "partial"
    snapshot_without_notes = {**snapshot, "reviewObservations": []}
    assert "readiness partial" in _summary_paragraph(snapshot_without_notes)
    assert snapshot == before
    assert _hash_payload(snapshot) == original_hash


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "wrong_portfolio",
        "wrong_end",
        "missing_periods",
        "missing_bases",
        "missing_summary_value",
        "invalid_annualized",
        "excluded_gross_value",
        "malformed",
    ],
)
def test_retained_qualification_requires_snapshot_binding(case):
    qualification = qualify(source_facts())
    snapshot = {
        "portfolio_id": "HISTORY_OWNER",
        "as_of_date": "2026-01-09",
        "performance": {
            "summary": {"YTD": {"net_cumulative_return": 0.0}},
            "history_qualification": qualification,
        },
    }
    if case == "wrong_portfolio":
        snapshot["portfolio_id"] = "OTHER"
    elif case == "wrong_end":
        snapshot["as_of_date"] = "2026-01-10"
    elif case == "missing_periods":
        qualification["requested_periods"] = []
    elif case == "missing_bases":
        qualification["period_return_bases"] = {}
    elif case == "missing_summary_value":
        snapshot["performance"]["summary"]["YTD"]["net_cumulative_return"] = None
    elif case == "invalid_annualized":
        snapshot["performance"]["summary"]["YTD"]["net_annualized_return"] = True
    elif case == "excluded_gross_value":
        snapshot["performance"]["summary"]["YTD"]["gross_cumulative_return"] = 7.25
    elif case == "malformed":
        qualification["coverage"]["reason_codes"] = ["unbounded source reason"]
    before = copy.deepcopy(snapshot)
    result = snapshot_history_qualification(snapshot)
    assert result["client_publication_allowed"] is (case == "valid")
    if case == "malformed":
        assert result["status"] == "invalid"
    assert snapshot == before
