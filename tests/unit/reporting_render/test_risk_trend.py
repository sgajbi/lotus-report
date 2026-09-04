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
) -> dict[str, Any]:
    metrics = metrics or ["ROLLING_VOLATILITY", "ROLLING_BETA", "ROLLING_TRACKING_ERROR"]
    block: dict[str, Any] = {
        "source": {"service": "lotus-risk", "endpoint": "/analytics/risk/rolling-metrics"},
        "request": {"window_observations": 63, "metrics": metrics, "frequency": "daily"},
        "supportability": supportability or {"status": "ready", "notes": []},
        "results": results if results is not None else ({"YTD": period} if period else {}),
        "metadata": {},
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
    # The warm-up null is a visible gap: the date simply has no entry, and
    # nothing is filled in its place.
    assert volatility["series"] == [
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
    # Only the two well-formed numeric points survive; nothing is coerced or
    # invented for the malformed ones.
    assert volatility["series"] == [
        {"date": "2026-03-05", "value": "0.15"},
        {"date": "2026-03-06", "value": "0.16"},
    ]
