"""The report#289 drawdown block - the contract locked with Render.

Source-owned series and episodes, complete (presentation capping is
Render's), open episodes stated open, values verbatim decimal fractions,
and a posture grammar where ready requires the SERIES while episodes may
be empty (visible calm).
"""

from __future__ import annotations

from app.reporting_render.package_builder import _drawdown_section


def _captured(results: dict | None = None, *, status: str = "ready", notes=None) -> dict:
    return {
        "drawdown": {
            "source": {"service": "lotus-risk", "endpoint": "/analytics/risk/drawdown"},
            "request": {"period": "1Y", "net_or_gross": "NET", "include_underwater_series": True},
            "supportability": {"status": status, "notes": notes or []},
            "results": results or {},
            "metadata": {
                "product_name": "DrawdownAnalyticsReport",
                "product_version": "v1",
                "contract_version": "v1",
                "methodology_version": "drawdown.v1",
                "duration_unit": "BUSINESS_DAYS",
            },
        }
    }


def _ready_period() -> dict:
    return {
        "1Y": {
            "start_date": "2025-02-24",
            "end_date": "2026-02-24",
            "summary": {
                "max_drawdown": -0.124533,
                "max_drawdown_peak_date": "2026-01-12",
                "max_drawdown_trough_date": "2026-02-03",
                "max_drawdown_recovery_date": None,
            },
            "episodes": [
                {
                    "episode_id": "dd_0001",
                    "peak_date": "2025-04-01",
                    "trough_date": "2025-04-20",
                    "recovery_date": "2025-05-11",
                    "depth": -0.0612,
                    "days_to_trough": 13,
                },
                {
                    "episode_id": "dd_0002",
                    "peak_date": "2026-01-12",
                    "trough_date": "2026-02-03",
                    "recovery_date": None,
                    "depth": -0.124533,
                    "days_to_trough": 16,
                },
            ],
            "underwater_series": [
                {"date": "2026-01-13", "drawdown": -0.0121},
                {"date": "2026-02-03", "drawdown": -0.124533},
            ],
            "error": None,
        }
    }


def test_a_stated_series_emits_ready_with_verbatim_values_and_open_episode() -> None:
    block = _drawdown_section(_captured(_ready_period()))

    assert block["posture"] == "ready"
    assert block["value_unit"] == "decimal_fraction"
    assert block["duration_unit"] == "BUSINESS_DAYS"
    assert block["methodology_version"] == "drawdown.v1"
    # Values are the source's JSON numbers round-tripped verbatim.
    assert block["underwater"] == [
        {"date": "2026-01-13", "drawdown": "-0.0121"},
        {"date": "2026-02-03", "drawdown": "-0.124533"},
    ]
    assert block["episodes"][0]["recovery_date"] == "2025-05-11"
    # An unrecovered drawdown stays OPEN - null, never closed-looking.
    assert block["episodes"][1]["recovery_date"] is None
    assert block["episodes"][1]["depth"] == "-0.124533"
    assert block["episodes"][1]["days_to_trough"] == 16
    assert block["summary"] == {
        "max_drawdown": "-0.124533",
        "max_drawdown_peak_date": "2026-01-12",
        "max_drawdown_trough_date": "2026-02-03",
        "max_drawdown_recovery_date": None,
    }
    assert "source_statement" not in block


def test_a_calm_window_is_ready_with_zero_episodes() -> None:
    results = _ready_period()
    results["1Y"]["episodes"] = []
    results["1Y"]["summary"] = None
    block = _drawdown_section(_captured(results))

    assert block["posture"] == "ready"
    assert block["episodes"] == []
    assert block["summary"] is None
    assert "source_statement" not in block


def test_a_window_the_source_states_no_series_for_reads_empty() -> None:
    results = _ready_period()
    results["1Y"]["underwater_series"] = []
    block = _drawdown_section(_captured(results))

    assert block["posture"] == "empty"
    assert block["underwater"] == []
    assert "source_statement" not in block


def test_an_upstream_refusal_reads_unavailable_in_the_sources_voice() -> None:
    block = _drawdown_section(
        _captured(
            status="unavailable",
            notes=[
                {
                    "code": "drawdown_upstream_failure",
                    "severity": "blocking",
                    "message": (
                        "Drawdown analytics are unavailable because lotus-risk "
                        "could not calculate them."
                    ),
                }
            ],
        )
    )

    assert block["posture"] == "unavailable"
    assert block["source_statement"] == (
        "Drawdown analytics are unavailable because lotus-risk could not calculate them."
    )


def test_a_period_the_source_marks_in_error_is_said_not_drawn() -> None:
    results = _ready_period()
    results["1Y"]["underwater_series"] = [{"date": "2026-01-13", "drawdown": -0.01}]
    results["1Y"]["error"] = "Insufficient data"
    block = _drawdown_section(_captured(results))

    assert block["posture"] == "unavailable"
    assert block["source_statement"] == "Insufficient data"
    assert block["underwater"] == []


def test_a_missing_period_reads_unavailable_with_owned_copy() -> None:
    block = _drawdown_section(_captured({}))

    assert block["posture"] == "unavailable"
    assert block["source_statement"] == "Drawdown analytics were not sourced for this report."


def test_a_pre_contract_snapshot_makes_no_panel_claim() -> None:
    assert _drawdown_section({}) == {}
