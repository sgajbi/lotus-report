"""report_data.risk_attribution - the #254 contract, one behaviour per test.

Every stated fact is source-owned: the reconciliation triple required
together with the residual presented (never allocated away), contributors in
source order with no Report-side ranking, refusals in the source's voice,
and the source's unit statement on ready sets. weight_average and
percent_contribution are decimal fractions of one structurally.
"""

from __future__ import annotations

from typing import Any

from app.reporting_render.package_builder import _risk_attribution_section


def _contributor(key: str, component: float, percent: float, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "group_key": key,
        "group_label": key.title(),
        "component_contribution": component,
        "percent_contribution": percent,
    }
    row.update(extra)
    return row


def _source_set(
    attribution_type: str = "TOTAL_RISK",
    metric: str = "VOLATILITY",
    *,
    contributors: list[dict[str, Any]] | None = None,
    total: float | None = 0.1253,
    reconciled: float | None = 0.1249,
    residual: float | None = 0.0004,
    quality_flags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "attribution_type": attribution_type,
        "metric": metric,
        "grouping_dimension": "SECTOR",
        "total_value": total,
        "reconciled_sum": reconciled,
        "residual": residual,
        "contributors": (
            contributors
            if contributors is not None
            else [
                _contributor("SECTOR_TECH", 0.0784, 0.6258, weight_average=0.245),
                _contributor("SECTOR_FIN", -0.0112, -0.0894, marginal_contribution=-0.031),
            ]
        ),
        "quality_flags": quality_flags or [],
    }


def _snapshot(
    *,
    benchmarked: bool = True,
    supportability: dict[str, Any] | None = None,
    period: dict[str, Any] | None = None,
    results: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    types = ["TOTAL_RISK"] + (["ACTIVE_RISK"] if benchmarked else [])
    metrics = ["VOLATILITY"] + (["TRACKING_ERROR"] if benchmarked else [])
    if metadata is None:
        metadata = {
            "metric_unit_semantics": {
                "VOLATILITY": "decimal_ratio",
                "TRACKING_ERROR": "decimal_ratio",
            },
            "benchmark_context": {"requested": benchmarked, "reason": "APPLIED"},
            "stateful_active_risk_gate_reason": "none",
        }
    return {
        "riskAttribution": {
            "source": {
                "service": "lotus-risk",
                "endpoint": "/analytics/risk/historical-attribution",
            },
            "request": {
                "attribution_types": types,
                "metrics": metrics,
                "grouping_dimension": "SECTOR",
            },
            "supportability": supportability or {"status": "ready", "notes": []},
            "results": results if results is not None else ({"YTD": period} if period else {}),
            "metadata": metadata,
        }
    }


def _period(sets: list[dict[str, Any]], *, error: str | None = None) -> dict[str, Any]:
    return {
        "start_date": "2026-01-02",
        "end_date": "2026-08-31",
        "attribution_sets": sets,
        "error": error,
    }


def test_an_unordered_section_draws_no_panel() -> None:
    assert _risk_attribution_section({}) == {}


def test_a_ready_set_states_the_reconciliation_triple_and_source_order() -> None:
    section = _risk_attribution_section(
        _snapshot(
            period=_period(
                [
                    _source_set(),
                    _source_set(
                        "ACTIVE_RISK",
                        "TRACKING_ERROR",
                        total=0.021,
                        reconciled=0.0208,
                        residual=0.0002,
                    ),
                ]
            )
        )
    )

    assert section["window"]["period"] == {
        "name": "YTD",
        "start_date": "2026-01-02",
        "end_date": "2026-08-31",
    }
    total_risk = section["sets"][0]
    assert total_risk["posture"] == "ready"
    assert total_risk["unit"] == "decimal_ratio"
    assert (total_risk["total_value"], total_risk["reconciled_sum"], total_risk["residual"]) == (
        "0.1253",
        "0.1249",
        "0.0004",
    )
    # Source order preserved, no ranking; the negative contributor keeps its
    # sign; optional fields appear only when the source stated them.
    assert [row["group_key"] for row in total_risk["contributors"]] == [
        "SECTOR_TECH",
        "SECTOR_FIN",
    ]
    assert total_risk["contributors"][0]["weight_average"] == "0.245"
    assert "marginal_contribution" not in total_risk["contributors"][0]
    assert total_risk["contributors"][1]["component_contribution"] == "-0.0112"
    active = section["sets"][1]
    assert active["attribution_type"] == "ACTIVE_RISK"
    assert active["posture"] == "ready"


def test_an_upstream_failure_refuses_every_set_with_the_stated_reason() -> None:
    section = _risk_attribution_section(
        _snapshot(
            supportability={
                "status": "unavailable",
                "notes": [{"code": "risk_attribution_upstream_failure", "severity": "blocking"}],
            }
        )
    )

    assert [item["posture"] for item in section["sets"]] == ["unavailable", "unavailable"]
    assert all(
        item["notes"][0]["code"] == "risk_attribution_upstream_failure" for item in section["sets"]
    )


def test_a_period_the_source_marks_in_error_is_said_not_drawn() -> None:
    section = _risk_attribution_section(_snapshot(period=_period([], error="Insufficient data")))

    for item in section["sets"]:
        assert item["posture"] == "unavailable"
        assert item["notes"][0] == {
            "code": "risk_attribution_source_error",
            "message": "Insufficient data",
        }


def test_active_risk_on_an_unapplied_benchmark_is_stated_in_the_sources_voice() -> None:
    metadata = {
        "metric_unit_semantics": {"VOLATILITY": "decimal_ratio", "TRACKING_ERROR": "decimal_ratio"},
        "benchmark_context": {"requested": True, "reason": "BENCHMARK_UNAVAILABLE"},
        "stateful_active_risk_gate_reason": "none",
    }
    section = _risk_attribution_section(
        _snapshot(period=_period([_source_set()]), metadata=metadata)
    )

    total_risk, active = section["sets"]
    assert total_risk["posture"] == "ready"
    assert active["posture"] == "unavailable"
    assert active["notes"][0] == {
        "code": "benchmark_not_applied",
        "message": "BENCHMARK_UNAVAILABLE",
    }


def test_a_missing_set_is_refused_not_substituted() -> None:
    section = _risk_attribution_section(_snapshot(benchmarked=False, period=_period([])))

    only = section["sets"][0]
    assert only["posture"] == "unavailable"
    assert only["notes"][0]["code"] == "attribution_set_missing"


def test_an_incomplete_reconciliation_triple_is_never_drawn() -> None:
    section = _risk_attribution_section(
        _snapshot(
            benchmarked=False,
            period=_period([_source_set(residual=None, quality_flags=["LOW_COVERAGE"])]),
        )
    )

    only = section["sets"][0]
    assert only["posture"] == "unavailable"
    assert only["notes"][0]["code"] == "attribution_reconciliation_incomplete"
    assert only["quality_flags"] == ["LOW_COVERAGE"]


def test_a_partial_contributor_row_states_the_whole_set() -> None:
    rows = [
        _contributor("SECTOR_TECH", 0.0784, 0.6258),
        {"group_key": "SECTOR_FIN", "component_contribution": None, "percent_contribution": 0.1},
    ]
    section = _risk_attribution_section(
        _snapshot(benchmarked=False, period=_period([_source_set(contributors=rows)]))
    )

    only = section["sets"][0]
    assert only["posture"] == "unavailable"
    assert only["notes"][0]["code"] == "attribution_contributors_incomplete"


def test_a_set_without_contributors_is_empty_with_the_gate_reason() -> None:
    metadata = {
        "metric_unit_semantics": {"VOLATILITY": "decimal_ratio", "TRACKING_ERROR": "decimal_ratio"},
        "benchmark_context": {"requested": True, "reason": "APPLIED"},
        "stateful_active_risk_gate_reason": "position_returns_unavailable",
    }
    section = _risk_attribution_section(
        _snapshot(
            benchmarked=False,
            period=_period([_source_set(contributors=[])]),
            metadata=metadata,
        )
    )

    only = section["sets"][0]
    assert only["posture"] == "empty"
    assert only["notes"][0]["message"] == "position_returns_unavailable"


def test_a_missing_period_refuses_every_set() -> None:
    section = _risk_attribution_section(_snapshot(results={}))

    for item in section["sets"]:
        assert item["posture"] == "unavailable"
        assert item["notes"][0]["code"] == "risk_attribution_period_missing"


def test_a_ready_set_without_a_stated_unit_carries_no_unit_key() -> None:
    metadata = {
        "metric_unit_semantics": {},
        "benchmark_context": {"requested": False, "reason": "APPLIED"},
        "stateful_active_risk_gate_reason": "none",
    }
    section = _risk_attribution_section(
        _snapshot(benchmarked=False, period=_period([_source_set()]), metadata=metadata)
    )

    only = section["sets"][0]
    assert only["posture"] == "ready"
    assert "unit" not in only
