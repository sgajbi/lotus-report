"""The extracted workspace-series projections state facts or nothing."""

from __future__ import annotations

from app.services.workspace_performance import (
    benchmark_source_statement,
    return_base,
    workspace_benchmark_history,
    workspace_breakdowns,
    workspace_performance_history,
)


def test_return_base_reads_numbers_strings_and_refuses_garbage() -> None:
    assert return_base({"period_return": {"base": 1.5}}, "period_return") == 1.5
    assert return_base({"period_return": {"base": 2}}, "period_return") == 2.0
    assert return_base({"period_return": {"base": "3.25"}}, "period_return") == 3.25
    assert return_base({"period_return": {"base": "not-a-number"}}, "period_return") is None
    assert return_base({"period_return": {}}, "period_return") is None
    assert return_base({}, "period_return") is None


def test_breakdowns_and_histories_state_nothing_for_malformed_shapes() -> None:
    assert workspace_breakdowns({}, frequency="monthly") == []
    assert (
        workspace_breakdowns(
            {"portfolio_twr": {"net": {"breakdowns": {"monthly": "x"}}}}, frequency="monthly"
        )
        == []
    )
    assert (
        workspace_performance_history(results_by_period={}, period_name="1Y", frequency="monthly")
        == []
    )
    assert (
        workspace_benchmark_history(results_by_period={}, period_name="1Y", frequency="monthly")
        == []
    )
    assert (
        workspace_benchmark_history(
            results_by_period={"1Y": {"benchmark": {"breakdowns": {"monthly": "x"}}}},
            period_name="1Y",
            frequency="monthly",
        )
        == []
    )


def test_history_parses_string_economics_and_defaults_garbage_to_zero() -> None:
    history = workspace_performance_history(
        results_by_period={
            "1Y": {
                "portfolio_twr": {
                    "net": {
                        "breakdowns": {
                            "monthly": [
                                {
                                    "period": "2026-01",
                                    "economics": {
                                        "begin_market_value": "100.0",
                                        "flow_adjusted_end_market_value": "110.0",
                                        "beginning_cash_flow": "garbage",
                                    },
                                    "period_return": {"base": "1.0"},
                                }
                            ]
                        }
                    }
                }
            }
        },
        period_name="1Y",
        frequency="monthly",
    )

    assert history[0]["performance_value"] == 10.0
    assert history[0]["inflows"] == 0
    assert history[0]["twr_pct"] == 1.0


def test_source_statement_is_the_benchmark_note_verbatim_or_nothing() -> None:
    assert benchmark_source_statement({}) is None
    assert benchmark_source_statement({"diagnostics": {"notes": "not-a-list"}}) is None
    assert (
        benchmark_source_statement({"diagnostics": {"notes": ["Prices interpolated for 2 days."]}})
        is None
    )
    assert (
        benchmark_source_statement(
            {"diagnostics": {"notes": [{"ignored": True}, "Benchmark disabled for 1Y."]}}
        )
        == "Benchmark disabled for 1Y."
    )
