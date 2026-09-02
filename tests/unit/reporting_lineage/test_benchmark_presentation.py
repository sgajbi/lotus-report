"""A report says whether it promised a benchmark comparison (issue #241)."""

from app.reporting_lineage.benchmark_presentation import (
    REASON_UNPROVEN_ON_REPLAY,
    resolve_benchmark_presentation,
)


def _snapshot(benchmark, notes=None):
    return {
        "performance": {
            "benchmark": benchmark,
            "supportability": {"notes": notes or []},
        }
    }


def test_an_unbenchmarked_mandate_and_a_failed_comparison_are_different_reports():
    """The whole point. Both produce a period table whose benchmark values are
    all absent, so a renderer inferring from the values reads the second as the
    first - and silently removes the columns a benchmarked mandate's reader is
    entitled to, with nothing on the page to ask about.
    """

    unbenchmarked = resolve_benchmark_presentation(
        options={}, snapshot=_snapshot({"comparison_status": "not_requested"})
    )
    failed = resolve_benchmark_presentation(
        options={},
        snapshot=_snapshot(
            {
                "comparison_status": "unavailable",
                "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
                "reason_code": "benchmark_return_series_not_sourced",
            },
            notes=[
                {
                    "code": "benchmark_comparison_unavailable",
                    "severity": "warning",
                    "message": "Benchmark comparison is unavailable.",
                }
            ],
        ),
    )

    assert unbenchmarked["posture"] != failed["posture"]
    assert unbenchmarked["benchmark_code"] is None
    assert failed["benchmark_code"] == "BMK_PB_GLOBAL_BALANCED_60_40"
    assert failed["reason_code"] == "benchmark_return_series_not_sourced"
    assert failed["notes"][0]["code"] == "benchmark_comparison_unavailable"


def test_each_stated_posture_is_forwarded_verbatim():
    """The capture's own vocabulary, not a second set of names for one fact."""

    for status in ("available", "unavailable", "not_requested"):
        presentation = resolve_benchmark_presentation(
            options={}, snapshot=_snapshot({"comparison_status": status})
        )
        assert presentation["posture"] == status


def test_a_sourced_comparison_names_what_it_compared_against():
    presentation = resolve_benchmark_presentation(
        options={},
        snapshot=_snapshot(
            {
                "comparison_status": "available",
                "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
                "return_source": "INDEX_TOTAL_RETURN",
            }
        ),
    )

    assert presentation["posture"] == "available"
    assert presentation["return_source"] == "INDEX_TOTAL_RETURN"
    assert presentation["reason_code"] is None


def test_a_capture_predating_the_context_resolves_from_the_order():
    """A rerender is exactly the case most likely to matter, and reading the
    table's values would restate the bug for it. The order is the evidence:
    it asked for a benchmark, so the columns stay - but the comparison is not
    claimed as sourced, because that was never proven for this capture."""

    replayed = resolve_benchmark_presentation(
        options={"benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40"},
        snapshot={"performance": {"summary": {"YTD": {}}}},
    )

    assert replayed["posture"] == "unavailable"
    assert replayed["benchmark_code"] == "BMK_PB_GLOBAL_BALANCED_60_40"
    assert replayed["reason_code"] == REASON_UNPROVEN_ON_REPLAY


def test_a_capture_predating_the_context_with_no_benchmark_ordered_draws_none():
    """The order asked for nothing, so nothing is promised and no columns are
    owed - the one case where absence is the honest answer."""

    replayed = resolve_benchmark_presentation(
        options={}, snapshot={"performance": {"summary": {"YTD": {}}}}
    )

    assert replayed["posture"] == "not_requested"
    assert replayed["reason_code"] is None


def test_an_unrecognised_status_never_reads_as_unbenchmarked():
    """Failing toward `not_requested` would silently drop the columns, which
    is the defect. An unrecognised status resolves from the order instead."""

    presentation = resolve_benchmark_presentation(
        options={"benchmark_code": "BMK"},
        snapshot=_snapshot({"comparison_status": "mostly_fine"}),
    )

    assert presentation["posture"] == "unavailable"


def test_a_report_without_a_performance_section_promises_nothing():
    assert resolve_benchmark_presentation(options={}, snapshot={}) == {}
