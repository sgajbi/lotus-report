"""Workspace-summary series extraction (portfolio and benchmark buckets).

Extracted from the reporting read service: these are pure projections of
lotus-performance's shipped WorkspaceSummaryResponse into the captured
performance section - the portfolio's economics-bearing monthly history and
the benchmark's economics-free bucket series (report#288). Everything here
forwards the OWNER's stated facts; nothing derives, links, or gap-fills.
"""

from __future__ import annotations


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _safe_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _to_float(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def return_base(summary_payload: dict[str, object], key: str) -> float | None:
    """The 'base' reading of one WorkspaceReturnValue, or None when unstated."""

    return_value = _as_dict(summary_payload.get(key)).get("base")
    if isinstance(return_value, (int, float)):
        return float(return_value)
    if isinstance(return_value, str):
        try:
            return float(return_value)
        except ValueError:
            return None
    return None


def workspace_breakdowns(period_payload: dict[str, object], *, frequency: str) -> list[object]:
    portfolio_twr = _as_dict(period_payload.get("portfolio_twr"))
    net_block = _as_dict(portfolio_twr.get("net"))
    breakdowns = _as_dict(net_block.get("breakdowns"))
    items = breakdowns.get(frequency)
    return items if isinstance(items, list) else []


def workspace_performance_history(
    *,
    results_by_period: dict[str, object],
    period_name: str,
    frequency: str,
) -> list[dict[str, object]]:
    period_payload = _as_dict(results_by_period.get(period_name))
    history: list[dict[str, object]] = []
    cumulative_value = 0.0
    for item in workspace_breakdowns(period_payload, frequency=frequency):
        row = _as_dict(item)
        economics = _as_dict(row.get("economics"))
        begin_market_value = _to_float(economics.get("begin_market_value"))
        end_market_value = _to_float(economics.get("end_market_value"))
        beginning_cash_flow = _to_float(economics.get("beginning_cash_flow"))
        ending_cash_flow = _to_float(economics.get("ending_cash_flow"))
        net_cash_flow = _to_float(economics.get("net_cash_flow"))
        flow_adjusted_end_value = _to_float(economics.get("flow_adjusted_end_market_value"))
        performance_value = flow_adjusted_end_value - begin_market_value
        cumulative_value += performance_value
        inflows = sum(amount for amount in (beginning_cash_flow, ending_cash_flow) if amount > 0)
        outflows = sum(amount for amount in (beginning_cash_flow, ending_cash_flow) if amount < 0)
        history.append(
            {
                "period": _safe_str(row.get("period")),
                "period_start": _safe_str(row.get("period_start")),
                "period_end": _safe_str(row.get("period_end")),
                "begin_market_value": begin_market_value,
                "end_market_value": end_market_value,
                "inflows": inflows,
                "outflows": outflows,
                "net_cash_flow": net_cash_flow,
                "performance_value": performance_value,
                "cumulative_performance_value": cumulative_value,
                "twr_pct": return_base(row, "period_return"),
                "cumulative_twr_pct": return_base(row, "cumulative_return"),
                "annualized_twr_pct": return_base(row, "annualized_return"),
            }
        )
    return history


def workspace_benchmark_history(
    *,
    results_by_period: dict[str, object],
    period_name: str,
    frequency: str,
) -> list[dict[str, object]]:
    """The benchmark's own bucket series, the OWNER's facts verbatim.

    WorkspaceBenchmarkBlock states no economics and returns base-only
    values, so a benchmark bucket carries exactly the source-stated period
    identity (label + inclusive bucket dates) and the two return readings.
    A month the source did not state is simply absent from the list.
    """

    period_payload = _as_dict(results_by_period.get(period_name))
    benchmark = _as_dict(period_payload.get("benchmark"))
    breakdowns = _as_dict(benchmark.get("breakdowns"))
    items = breakdowns.get(frequency)
    history: list[dict[str, object]] = []
    for item in items if isinstance(items, list) else []:
        row = _as_dict(item)
        history.append(
            {
                "period": _safe_str(row.get("period")),
                "period_start": _safe_str(row.get("period_start")),
                "period_end": _safe_str(row.get("period_end")),
                "twr_pct": return_base(row, "period_return"),
                "cumulative_twr_pct": return_base(row, "cumulative_return"),
            }
        )
    return history


def benchmark_source_statement(payload: dict[str, object]) -> str | None:
    """The source's own benchmark diagnostics note, verbatim.

    When lotus-performance explains a benchmark posture in its diagnostics
    notes, that sentence IS the statement the document should carry - the
    source's voice, never a Report paraphrase."""

    diagnostics = _as_dict(payload.get("diagnostics"))
    notes = diagnostics.get("notes")
    for note in notes if isinstance(notes, list) else []:
        if isinstance(note, str) and "benchmark" in note.lower():
            return note
    return None


def performance_benchmark_context(
    *,
    requested_benchmark_code: str | None,
    aliases: dict[str, str],
    available: bool = False,
    resolved_benchmark_code: str | None = None,
    return_source: str | None = None,
    benchmark_currency: str | None = None,
    source_statement: str | None = None,
) -> dict[str, object]:
    benchmark_code = (
        aliases.get(requested_benchmark_code, requested_benchmark_code)
        if requested_benchmark_code is not None
        else None
    )
    if available:
        return {
            "benchmark_code": resolved_benchmark_code or benchmark_code,
            "requested_benchmark_code": requested_benchmark_code,
            "comparison_status": "available",
            "return_source": return_source,
            "benchmark_currency": benchmark_currency,
            "reason_code": None,
        }
    return {
        "benchmark_code": benchmark_code,
        "comparison_status": "unavailable" if benchmark_code else "not_requested",
        "reason_code": "benchmark_return_series_not_sourced" if benchmark_code else None,
        "source_statement": source_statement,
    }


def performance_supportability(
    *,
    benchmark_requested: bool,
    benchmark_available: bool = False,
) -> dict[str, object]:
    if not benchmark_requested or benchmark_available:
        return {"status": "ready", "notes": []}
    return {
        "status": "partial",
        "notes": [
            {
                "code": "benchmark_comparison_unavailable",
                "severity": "warning",
                "message": (
                    "Benchmark comparison is unavailable because benchmark return series "
                    "is not sourced in this report response."
                ),
            }
        ],
    }
