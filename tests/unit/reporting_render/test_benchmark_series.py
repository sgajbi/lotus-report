"""The report#288 benchmark series block - the contract locked with Render.

A separate additive block beside the portfolio rows, owner field names
verbatim (benchmark_id; input_mode dropped deliberately), and a posture
that is never an empty line.
"""

from __future__ import annotations

from app.reporting_render.package_builder import _benchmark_series_section


def _snapshot(performance: dict) -> dict:
    return {"performance": performance}


def _ready_performance() -> dict:
    return {
        "benchmark": {
            "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
            "comparison_status": "available",
            "return_source": "calculated",
            "benchmark_currency": "USD",
        },
        "benchmark_monthly_history": [
            {
                "period": "2026-01",
                "period_start": "2026-01-01",
                "period_end": "2026-01-31",
                "twr_pct": -1.21,
                "cumulative_twr_pct": -1.21,
            },
            {
                "period": "2026-02",
                "period_start": "2026-02-01",
                "period_end": "2026-02-24",
                "twr_pct": 1.02,
                "cumulative_twr_pct": -0.2,
            },
        ],
    }


def test_a_stated_series_emits_ready_points_verbatim() -> None:
    block = _benchmark_series_section(_snapshot(_ready_performance()))

    assert block["posture"] == "ready"
    assert block["benchmark_id"] == "BMK_PB_GLOBAL_BALANCED_60_40"
    assert block["benchmark_currency"] == "USD"
    assert block["return_source"] == "calculated"
    # The portfolio rows' textual precision treatment, source values verbatim.
    assert block["points"] == [
        {
            "period": "2026-01",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "twr_pct": "-1.21%",
            "cumulative_twr_pct": "-1.21%",
        },
        {
            "period": "2026-02",
            "period_start": "2026-02-01",
            "period_end": "2026-02-24",
            "twr_pct": "1.02%",
            "cumulative_twr_pct": "-0.20%",
        },
    ]
    assert "source_statement" not in block


def test_the_series_shares_the_charts_twelve_month_window() -> None:
    performance = _ready_performance()
    performance["benchmark_monthly_history"] = [
        {
            "period": f"2025-{month:02d}",
            "period_start": f"2025-{month:02d}-01",
            "period_end": f"2025-{month:02d}-28",
            "twr_pct": 0.5,
            "cumulative_twr_pct": 0.5 * month,
        }
        for month in range(1, 13)
    ] + [
        {
            "period": "2026-01",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "twr_pct": 1.0,
            "cumulative_twr_pct": 7.0,
        }
    ]
    block = _benchmark_series_section(_snapshot(performance))

    assert len(block["points"]) == 12
    assert block["points"][-1]["period"] == "2026-01"
    assert block["points"][0]["period"] == "2025-02"


def test_an_unassigned_benchmark_is_a_normal_state_not_a_degradation() -> None:
    block = _benchmark_series_section(
        _snapshot(
            {
                "benchmark": {
                    "benchmark_code": None,
                    "comparison_status": "not_requested",
                    "reason_code": None,
                    "source_statement": None,
                },
                "benchmark_monthly_history": [],
            }
        )
    )

    assert block == {"posture": "unbenchmarked", "points": []}


def test_an_expected_but_unsourced_series_states_the_sources_sentence() -> None:
    block = _benchmark_series_section(
        _snapshot(
            {
                "benchmark": {
                    "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
                    "comparison_status": "unavailable",
                    "reason_code": "benchmark_return_series_not_sourced",
                    "source_statement": (
                        "Benchmark disabled for 1Y: benchmark return points end 2025-11-30."
                    ),
                },
                "benchmark_monthly_history": [],
            }
        )
    )

    assert block["posture"] == "unavailable"
    assert block["benchmark_id"] == "BMK_PB_GLOBAL_BALANCED_60_40"
    assert block["points"] == []
    assert block["source_statement"] == (
        "Benchmark disabled for 1Y: benchmark return points end 2025-11-30."
    )


def test_unavailable_without_a_source_sentence_uses_report_document_copy() -> None:
    """When the source stated nothing, the caption is REPORT-authored
    document copy - never API voice - because the same field carries
    verbatim source sentences and ours must read no worse."""

    block = _benchmark_series_section(
        _snapshot(
            {
                "benchmark": {
                    "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
                    "comparison_status": "unavailable",
                    "source_statement": None,
                },
                "benchmark_monthly_history": [],
            }
        )
    )

    assert block["posture"] == "unavailable"
    assert block["source_statement"] == "Benchmark return series was not sourced for this report."


def test_an_available_claim_with_no_points_reads_unavailable_never_an_empty_line() -> None:
    performance = _ready_performance()
    performance["benchmark_monthly_history"] = []
    block = _benchmark_series_section(_snapshot(performance))

    assert block["posture"] == "unavailable"
    assert block["points"] == []
    assert block["source_statement"]


def test_a_pre_contract_snapshot_reads_unbenchmarked() -> None:
    """Snapshots captured before report#288 carry NO benchmark series keys:
    the block states the normal portfolio-only posture rather than
    inventing an unavailable claim about a series nobody expected."""

    block = _benchmark_series_section(_snapshot({}))

    assert block == {"posture": "unbenchmarked", "points": []}
