"""report_data.risk_trend - the agreed #255 contract, one behaviour per test.

Everything the page states is a source-owned fact: the series verbatim with
visible gaps, the window and frequency beside it, per-metric posture from the
source's own coverage facts, and never a trend statement Report composed.
"""

from __future__ import annotations

from typing import Any

from app.reporting_render.package_builder import _risk_trend_section


def _snapshot(
    *,
    metrics: list[str] | None = None,
    supportability: dict[str, Any] | None = None,
    period: dict[str, Any] | None = None,
    results: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metrics = metrics or ["ROLLING_VOLATILITY", "ROLLING_BETA", "ROLLING_TRACKING_ERROR"]
    block: dict[str, Any] = {
        "source": {"service": "lotus-risk", "endpoint": "/analytics/risk/rolling-metrics"},
        "request": {"window_observations": 63, "metrics": metrics, "frequency": "daily"},
        "supportability": supportability or {"status": "ready", "notes": []},
        "results": results if results is not None else ({"YTD": period} if period else {}),
        "metadata": metadata or {},
    }
    return {"riskTrend": block}


def _period(
    *,
    series_points: list[dict[str, Any]] | None = None,
    series_reason: str = "INCLUDED",
    benchmark_reason: str = "APPLIED",
    quality_flags: list[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    included = series_reason == "INCLUDED"
    return {
        "start_date": "2026-01-01",
        "end_date": "2026-04-22",
        "benchmark_context": {
            "requested": True,
            "available": benchmark_reason == "APPLIED",
            "aligned": benchmark_reason == "APPLIED",
            "reason": benchmark_reason,
        },
        "quality_flags": quality_flags or [],
        "error": error,
        "window_results": [
            {
                "window_length": 63,
                "metric_summaries": {},
                "metric_series": series_points if included else None,
                "metric_series_context": {
                    "requested": True,
                    "included": included,
                    "emitted_point_count": len(series_points or []),
                    "reason": series_reason,
                },
            }
        ],
    }


def _points() -> list[dict[str, Any]]:
    return [
        {"date": "2026-03-02", "metric_values": {"ROLLING_VOLATILITY": None}},
        {
            "date": "2026-03-03",
            "metric_values": {
                "ROLLING_VOLATILITY": 0.1374,
                "ROLLING_BETA": 0.92,
                "ROLLING_TRACKING_ERROR": 0.021,
            },
        },
        {
            "date": "2026-03-04",
            "metric_values": {
                "ROLLING_VOLATILITY": 0.141,
                "ROLLING_BETA": 0.93,
                "ROLLING_TRACKING_ERROR": 0.02,
            },
        },
    ]


def test_a_ready_series_is_forwarded_verbatim_with_visible_gaps() -> None:
    section = _risk_trend_section(_snapshot(period=_period(series_points=_points())))

    assert section["window"] == {
        "window_observations": 63,
        "frequency": "daily",
        "period": {"name": "YTD", "start_date": "2026-01-01", "end_date": "2026-04-22"},
    }
    volatility = section["metrics"][0]
    assert volatility["metric"] == "ROLLING_VOLATILITY"
    assert volatility["posture"] == "ready"
    # The warm-up null is a source-stated observation slot: preserved as an
    # explicit gap point (value null AND the posture naming why, always
    # together), never dropped and never filled.
    assert volatility["series"] == [
        {"date": "2026-03-02", "value": None, "point_posture": "not_computed"},
        {"date": "2026-03-03", "value": "0.1374"},
        {"date": "2026-03-04", "value": "0.141"},
    ]
    # No trend statement exists anywhere: the source does not state one, so
    # nobody does.
    assert "trend_statement" not in volatility


def test_an_unordered_risk_section_draws_no_panel() -> None:
    assert _risk_trend_section({}) == {}


def test_an_upstream_failure_refuses_every_metric_with_the_stated_reason() -> None:
    section = _risk_trend_section(
        _snapshot(
            supportability={
                "status": "unavailable",
                "notes": [{"code": "risk_trend_upstream_failure", "severity": "blocking"}],
            }
        )
    )

    assert [metric["posture"] for metric in section["metrics"]] == ["unavailable"] * 3
    assert all(
        metric["notes"][0]["code"] == "risk_trend_upstream_failure" for metric in section["metrics"]
    )


def test_a_period_the_source_marks_in_error_is_said_not_drawn() -> None:
    section = _risk_trend_section(
        _snapshot(period=_period(error="Insufficient data", quality_flags=["LOW_COVERAGE"]))
    )

    for metric in section["metrics"]:
        assert metric["posture"] == "unavailable"
        assert metric["notes"][0]["message"] == "Insufficient data"
        assert metric["quality_flags"] == ["LOW_COVERAGE"]


def test_benchmark_relative_series_on_an_unapplied_benchmark_are_stated_unavailable() -> None:
    section = _risk_trend_section(
        _snapshot(period=_period(series_points=_points(), benchmark_reason="BENCHMARK_UNAVAILABLE"))
    )

    by_metric = {metric["metric"]: metric for metric in section["metrics"]}
    assert by_metric["ROLLING_VOLATILITY"]["posture"] == "ready"
    for name in ("ROLLING_BETA", "ROLLING_TRACKING_ERROR"):
        assert by_metric[name]["posture"] == "unavailable"
        assert by_metric[name]["notes"][0] == {
            "code": "benchmark_not_applied",
            "message": "BENCHMARK_UNAVAILABLE",
        }


def test_a_series_the_source_did_not_include_is_empty_with_its_reason() -> None:
    section = _risk_trend_section(_snapshot(period=_period(series_reason="NO_METRIC_SERIES")))

    for metric in section["metrics"]:
        assert metric["posture"] == "empty"
        assert metric["notes"][0]["message"] == "NO_METRIC_SERIES"


def test_a_single_computed_point_cannot_claim_a_trend() -> None:
    single = [
        {"date": "2026-03-03", "metric_values": {"ROLLING_VOLATILITY": 0.1374}},
        {"date": "2026-03-04", "metric_values": {"ROLLING_VOLATILITY": None}},
    ]
    section = _risk_trend_section(
        _snapshot(metrics=["ROLLING_VOLATILITY"], period=_period(series_points=single))
    )

    volatility = section["metrics"][0]
    assert volatility["posture"] == "unavailable"
    assert volatility["notes"][0]["code"] == "series_insufficient_for_trend"


def test_a_missing_window_result_is_refused_not_substituted() -> None:
    period = _period(series_points=_points())
    period["window_results"][0]["window_length"] = 21  # a window nobody asked for
    section = _risk_trend_section(_snapshot(metrics=["ROLLING_VOLATILITY"], period=period))

    volatility = section["metrics"][0]
    assert volatility["posture"] == "unavailable"
    assert volatility["notes"][0]["code"] == "risk_trend_window_missing"


def test_an_answer_without_the_requested_period_is_refused() -> None:
    section = _risk_trend_section(_snapshot(results={}))

    for metric in section["metrics"]:
        assert metric["posture"] == "unavailable"
        assert metric["notes"][0]["code"] == "risk_trend_period_missing"


def test_malformed_points_are_skipped_as_gaps_never_guessed() -> None:
    points: list[Any] = [
        "not-a-point",
        {"metric_values": {"ROLLING_VOLATILITY": 0.5}},  # no date: no entry
        {"date": "2026-03-03", "metric_values": {"ROLLING_VOLATILITY": True}},
        {"date": "2026-03-04", "metric_values": {"ROLLING_VOLATILITY": "0.14"}},
        {"date": "2026-03-05", "metric_values": {"ROLLING_VOLATILITY": 0.15}},
        {"date": "2026-03-06", "metric_values": {"ROLLING_VOLATILITY": 0.16}},
    ]
    section = _risk_trend_section(
        _snapshot(metrics=["ROLLING_VOLATILITY"], period=_period(series_points=points))
    )

    volatility = section["metrics"][0]
    assert volatility["posture"] == "ready"
    # The two well-formed numeric points survive byte-identically; malformed
    # shapes (no date, non-dict, non-numeric value) are skipped, never
    # coerced into gap points - a gap is a SOURCE-stated fact, not a repair.
    assert volatility["series"] == [
        {"date": "2026-03-05", "value": "0.15"},
        {"date": "2026-03-06", "value": "0.16"},
    ]


def test_the_sources_unit_statement_is_forwarded_verbatim_on_ready_metrics() -> None:
    """The cross-repo unit-semantics regression, producer side.

    lotus-risk's rolling volatility is an annualized decimal ratio: 0.1374
    MEANS 13.74%. The emission forwards the source's own unit statement so
    the renderer can print the percentage meaning - Report never rescales
    the values and never invents a unit the source did not state.
    """

    section = _risk_trend_section(
        _snapshot(
            period=_period(series_points=_points()),
            metadata={
                "annualization_basis": 252,
                "metric_unit_semantics": {
                    "ROLLING_VOLATILITY": "decimal_ratio",
                    "ROLLING_BETA": "unitless",
                    "ROLLING_TRACKING_ERROR": "decimal_ratio",
                },
            },
        )
    )

    by_metric = {metric["metric"]: metric for metric in section["metrics"]}
    assert by_metric["ROLLING_VOLATILITY"]["unit"] == "decimal_ratio"
    assert by_metric["ROLLING_BETA"]["unit"] == "unitless"
    assert by_metric["ROLLING_TRACKING_ERROR"]["unit"] == "decimal_ratio"
    # Values stay the source's raw decimal strings; the unit is what makes
    # them readable, not a rescale.
    assert by_metric["ROLLING_VOLATILITY"]["series"][1] == {"date": "2026-03-03", "value": "0.1374"}


def test_a_metric_without_a_stated_unit_carries_none_rather_than_a_guess() -> None:
    section = _risk_trend_section(_snapshot(period=_period(series_points=_points())))

    for metric in section["metrics"]:
        assert "unit" not in metric


def _computed(date: str, value: float) -> dict[str, Any]:
    return {"date": date, "metric_values": {"ROLLING_VOLATILITY": value}}


def _not_computed(date: str) -> dict[str, Any]:
    return {"date": date, "metric_values": {"ROLLING_VOLATILITY": None}}


def test_gap_shapes_are_distinguished_exactly_as_the_source_states_them() -> None:
    """The steering's five-way distinction, in one series: an absent calendar
    date emits NOTHING (the source stated no slot), an explicit null emits a
    gap point, warm-up and partial-end coverage are runs of gap points at
    the stated dates, and computed observations are value strings. Report
    classifies no calendars and generates no dates - the emitted slots are
    exactly the source's slots."""

    points = [
        _not_computed("2026-03-02"),  # warm-up
        _not_computed("2026-03-03"),  # warm-up
        _computed("2026-03-04", 0.14),
        # 2026-03-05 absent entirely: no slot stated, so no entry appears
        _computed("2026-03-06", 0.15),
        _not_computed("2026-03-09"),  # mid-series explicit gap
        _computed("2026-03-10", 0.16),
        _not_computed("2026-03-11"),  # partial end coverage
    ]
    section = _risk_trend_section(
        _snapshot(metrics=["ROLLING_VOLATILITY"], period=_period(series_points=points))
    )

    volatility = section["metrics"][0]
    assert volatility["posture"] == "ready"
    assert volatility["series"] == [
        {"date": "2026-03-02", "value": None, "point_posture": "not_computed"},
        {"date": "2026-03-03", "value": None, "point_posture": "not_computed"},
        {"date": "2026-03-04", "value": "0.14"},
        {"date": "2026-03-06", "value": "0.15"},
        {"date": "2026-03-09", "value": None, "point_posture": "not_computed"},
        {"date": "2026-03-10", "value": "0.16"},
        {"date": "2026-03-11", "value": None, "point_posture": "not_computed"},
    ]
    # The locked pair discipline: every point is either a computed value
    # string with no posture, or null WITH the posture - never one alone.
    for point in volatility["series"]:
        if point["value"] is None:
            assert point["point_posture"] == "not_computed"
        else:
            assert "point_posture" not in point


def test_gap_points_do_not_count_toward_a_trend() -> None:
    points = [
        _not_computed("2026-03-02"),
        _computed("2026-03-03", 0.14),
        _not_computed("2026-03-04"),
        _not_computed("2026-03-05"),
    ]
    section = _risk_trend_section(
        _snapshot(metrics=["ROLLING_VOLATILITY"], period=_period(series_points=points))
    )

    volatility = section["metrics"][0]
    assert volatility["posture"] == "unavailable"
    assert volatility["notes"][0]["code"] == "series_insufficient_for_trend"
    assert "1 computed point" in volatility["notes"][0]["message"]
