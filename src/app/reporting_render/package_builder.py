from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from numbers import Real
from typing import Any, Sequence

from app.report_ordering_catalogue.template_resolution import (
    GOVERNED_BRAND_VARIANT,
    GOVERNED_LOCALE,
    GOVERNED_RENDER_PACKAGE_VERSION,
    resolve_report_data_contract,
    resolve_report_family,
)
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_lineage.allocation_presentation import resolve_allocation_presentation
from app.reporting_lineage.benchmark_presentation import resolve_benchmark_presentation
from app.reporting_render.attribution_bridge import build_attribution_bridge
from app.reporting_render.contribution_ranking import build_contribution_ranking
from app.reporting_render.document_reference import mint_document_reference
from app.reporting_render.earnings_statement import build_earnings_statement
from app.reporting_render.holdings_presentation import build_holdings_presentation
from app.reporting_render.risk_methodology import build_risk_methodology
from app.reporting_render.risk_posture import build_risk_posture


def _build_render_package(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
    snapshot_id: str,
    report_revision_id: str | None = None,
) -> dict[str, Any]:
    if job.report_type == "proof_pack":
        return _build_proof_pack_render_package(
            job=job,
            snapshot=snapshot,
            render_job_id=render_job_id,
            snapshot_id=snapshot_id,
            report_revision_id=report_revision_id,
        )
    if job.report_type == "outcome_review":
        return _build_outcome_review_render_package(
            job=job,
            snapshot=snapshot,
            render_job_id=render_job_id,
            snapshot_id=snapshot_id,
            report_revision_id=report_revision_id,
        )
    if job.report_type == "rebalance_wave":
        return _build_wave_render_package(
            job=job,
            snapshot=snapshot,
            render_job_id=render_job_id,
            snapshot_id=snapshot_id,
            report_revision_id=report_revision_id,
        )
    client_profile = _as_dict(snapshot.get("clientProfile"))
    identity = _as_dict(client_profile.get("identity"))
    mandate_profile = _as_dict(client_profile.get("mandate_profile"))
    overview = _as_dict(snapshot.get("overview"))
    key_figures = _as_dict(snapshot.get("keyFigures"))
    allocation = _as_dict(key_figures.get("allocation"))
    portfolio_value = _as_dict(key_figures.get("portfolio_value"))
    performance = _as_dict(_as_dict(snapshot.get("keyFigures")).get("performance"))
    holdings = _as_dict(_as_dict(snapshot.get("keyFigures")).get("holdings"))
    risk = _as_dict(_as_dict(snapshot.get("keyFigures")).get("risk"))
    evidence = _as_dict(snapshot.get("evidence"))
    trust_metadata = _as_dict(evidence.get("trust_metadata"))
    reviewed_narrative = _reviewed_advisory_narrative(snapshot)
    advisor_memo = _advisor_proposal_memo(snapshot)
    advisor_commentary = _advisor_commentary(snapshot)
    currency = (
        _optional_str(snapshot.get("reportingCurrency"))
        or _optional_str(overview.get("currency"))
        or job.reporting_currency
        or "USD"
    )
    observations = _review_observations(snapshot, performance, risk, holdings)
    if not observations:
        observations = [
            "Portfolio review was rendered from the governed lotus-report snapshot.",
        ]
    report_data = {
        "client_name": (
            _optional_str(identity.get("client_name"))
            or _optional_str(_as_dict(snapshot.get("clientProfile")).get("display_name"))
            or "Client"
        ),
        "portfolio_name": _portfolio_name(job, snapshot),
        "as_of_date": job.as_of_date.isoformat(),
        "currency": currency,
        "total_value": str(_optional_decimal(overview.get("total_market_value")) or "0"),
        "summary_paragraph": _summary_paragraph(snapshot),
        "review_observations": observations,
        "review_period_label": _optional_str(_as_dict(snapshot.get("reviewPeriod")).get("label"))
        or "YTD",
        "mandate": _mandate_section(
            snapshot=snapshot,
            key_figures=key_figures,
            mandate_profile=mandate_profile,
            identity=identity,
        ),
        "portfolio_metrics": _portfolio_metrics_section(portfolio_value),
        "allocation_summary": _allocation_summary_section(allocation),
        "allocation_breakdowns": _allocation_breakdowns(snapshot),
        "allocation_presentation": _allocation_presentation(job=job, snapshot=snapshot),
        # Whether this report promised a benchmark comparison. Stated,
        # because an all-unavailable comparison and an unbenchmarked
        # mandate produce identical rows - and inferring from the rows
        # silently drops a benchmarked mandate's columns during an
        # upstream outage.
        "benchmark_presentation": resolve_benchmark_presentation(
            options=job.options, snapshot=snapshot
        ),
        # What the presented returns MEAN. The figures are net of fees and
        # the document never said so; the basis was carried only by a
        # field name, which no renderer is obliged to read.
        "performance_basis": _performance_basis_section(snapshot),
        # Why the portfolio outperformed: the Brinson bridge, total ->
        # named parts -> explicit residual -> reconciled sum. Every figure is
        # the source's; the residual is presented, never allocated away.
        "attribution_bridge": build_attribution_bridge(snapshot),
        "performance_periods": _performance_periods(snapshot),
        "performance_summary_table": _performance_summary_table(snapshot),
        "performance_monthly_history": _performance_history(
            snapshot,
            "monthly_history",
            limit=12,
        ),
        # The report#288 contract locked with Render: a SEPARATE additive
        # block beside the portfolio rows (the chart pairs points by period
        # keys, never row position), owner field names verbatim
        # (benchmark_id; input_mode dropped deliberately), and a posture
        # that is never an empty line.
        "benchmark_series": _benchmark_series_section(snapshot),
        "performance_annual_history": _performance_history(
            snapshot,
            "annual_history",
            limit=8,
        ),
        "performance_highlight": _performance_highlight_section(performance),
        "contribution_ranking": build_contribution_ranking(snapshot),
        "transaction_period_label": (
            f"From {job.as_of_date.replace(month=1, day=1).strftime('%d.%m.%Y')} "
            f"to {job.as_of_date.strftime('%d.%m.%Y')}"
        ),
        "risk_summary": _risk_summary_section(snapshot, risk),
        "risk_trend": _risk_trend_section(snapshot),
        "risk_attribution": _risk_attribution_section(snapshot),
        # Why a figure is missing, not merely that it is. Without this the
        # panel prints one "Not available" for five different facts - two of
        # which send an operator in opposite directions.
        "risk_posture": build_risk_posture(snapshot),
        # A tail-risk number without its basis is not interpretable: 2% at
        # 95% over one day and 2% at 99% over ten days are different
        # statements about the same portfolio.
        "risk_methodology": build_risk_methodology(snapshot),
        "top_holdings": _top_holdings(snapshot),
        # What that panel of five leaves out. A subset must never imply
        # completeness, and the weights not summing to 100% asks a reader
        # to notice arithmetic rather than telling them.
        "holdings_presentation": build_holdings_presentation(
            snapshot,
            ranked=_ranked_holdings(snapshot),
            limit=PRESENTED_HOLDING_LIMIT,
        ),
        "positions": _positions(snapshot),
        "transactions": _transactions(snapshot),
        # What the transaction table adds up to, stated beside it instead
        # of left to the reader's arithmetic. A truncated window makes
        # the sums a floor, not a total, and the statement says which.
        "earnings_statement": build_earnings_statement(snapshot),
        "governance_summary": _governance_summary_section(
            snapshot=snapshot,
            evidence=evidence,
            trust_metadata=trust_metadata,
        ),
        "reviewed_advisory_narrative": reviewed_narrative,
        "advisor_proposal_memo": advisor_memo,
        "advisor_commentary": advisor_commentary,
    }
    lineage_refs = [job.job_id]
    disclosure_refs = [_job_disclosure_baseline(job)]
    if reviewed_narrative["status"] == "included":
        lineage_refs.extend(_reviewed_narrative_lineage_refs(reviewed_narrative))
        disclosure_refs.extend(_reviewed_narrative_disclosure_refs(reviewed_narrative))
    if advisor_memo["status"] == "included":
        lineage_refs.extend(_advisor_memo_lineage_refs(advisor_memo))
        disclosure_refs.extend(_advisor_memo_disclosure_refs(advisor_memo))
    if advisor_commentary["status"] == "included":
        lineage_refs.extend(
            [
                str(advisor_commentary.get("run_id") or "not_available"),
                str(advisor_commentary.get("content_hash") or "not_available"),
            ]
        )
    return _render_package_envelope(
        job=job,
        snapshot=snapshot,
        render_job_id=render_job_id,
        snapshot_id=snapshot_id,
        report_revision_id=report_revision_id,
        report_data_contract_version=_job_report_data_contract(job),
        report_data=report_data,
        lineage_refs=_dedupe_strings(lineage_refs),
        disclosure_refs=_dedupe_strings(disclosure_refs),
    )


def _mandate_section(
    *,
    snapshot: dict[str, Any],
    key_figures: dict[str, Any],
    mandate_profile: dict[str, Any],
    identity: dict[str, Any],
) -> dict[str, Any]:
    return {
        "objective": _optional_str(_as_dict(key_figures.get("client_profile")).get("objective"))
        or _optional_str(_as_dict(snapshot.get("methodology")).get("investment_objective"))
        or "Objective not available in the governed snapshot.",
        "risk_exposure": _optional_str(mandate_profile.get("risk_exposure")) or "not_available",
        "booking_center_code": _optional_str(identity.get("booking_center_code"))
        or "not_available",
        "advisor_id": _optional_str(identity.get("advisor_id")) or "not_available",
    }


def _portfolio_metrics_section(portfolio_value: dict[str, Any]) -> dict[str, Any]:
    return {
        "invested_value": _decimal_text(
            portfolio_value.get("invested_market_value_reporting_currency")
        ),
        "cash_balance": _decimal_text(portfolio_value.get("cash_balance_reporting_currency")),
        "cash_weight_pct": _percent_text(portfolio_value.get("cash_weight_pct")),
    }


def _allocation_summary_section(allocation: dict[str, Any]) -> dict[str, Any]:
    return {
        "largest_asset_class_name": _optional_str(allocation.get("name")) or "Not available",
        "largest_asset_class_weight_pct": _percent_text(allocation.get("weight_pct")),
        "largest_asset_class_market_value": _decimal_text(
            allocation.get("market_value_reporting_currency")
        ),
        "largest_asset_class_position_count": _optional_int(allocation.get("position_count")),
    }


def _performance_highlight_section(performance: dict[str, Any]) -> dict[str, Any]:
    return {
        "largest_positive_contributor_name": _holding_name(
            _as_dict(performance.get("largest_positive_contributor"))
        )
        or "Not available",
        "largest_positive_contribution_pct": _percent_text(
            _as_dict(performance.get("largest_positive_contributor")).get("total_contribution_pct")
            or _as_dict(performance.get("largest_positive_contributor")).get("ytd_contribution_pct")
        ),
        "benchmark_comparison_status": _optional_str(performance.get("benchmark_comparison_status"))
        or "not_available",
    }


def _ytd_risk_metric(snapshot: dict[str, Any], metric: str) -> Any:
    return (
        _as_dict(_as_dict(snapshot.get("riskAnalytics")).get("summary")).get("YTD", {}).get(metric)
    )


def _first_stated(*values: Any) -> Any:
    """The first value the capture actually stated, treating 0 as stated.

    The plain `a or b` this replaces read a legitimate zero as absence. That is
    harmless where both sources agree, and wrong for drawdown in particular: a
    portfolio that never fell during the period has a drawdown of exactly 0,
    which is a finding, not a gap. Rendering it as "Not available" would tell a
    reader we could not measure the thing we measured.
    """

    for value in values:
        if value is not None:
            return value
    return None


def _risk_summary_section(snapshot: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
    """The risk panel, or nothing at all when the report did not order risk.

    `riskAnalytics` is composed only when RISK_ANALYTICS is among the requested
    sections, so its absence means the caller deselected the section - not that
    a measurement failed. Emitting the five keys regardless drew a "Risk
    profile" panel of "Not available" cells onto reports that never asked for
    one, which reads as an attempted measurement that came back empty. In a
    governed client document that is a statement about the portfolio, and it
    was never true.

    Render already draws nothing for an empty mapping, so an unordered section
    simply has no panel.
    """

    if not _as_dict(snapshot.get("riskAnalytics")):
        return {}
    return {
        "volatility_pct": _percent_text(
            _first_stated(_ytd_risk_metric(snapshot, "volatility"), risk.get("ytd_volatility_pct"))
        ),
        "beta": _decimal_text(
            _first_stated(_ytd_risk_metric(snapshot, "beta"), risk.get("ytd_beta"))
        ),
        "tracking_error_pct": _percent_text(
            _first_stated(
                _ytd_risk_metric(snapshot, "tracking_error"), risk.get("ytd_tracking_error_pct")
            )
        ),
        "information_ratio": _decimal_text(
            _first_stated(
                _ytd_risk_metric(snapshot, "information_ratio"), risk.get("ytd_information_ratio")
            )
        ),
        "value_at_risk_pct": _percent_text(
            _first_stated(
                _ytd_risk_metric(snapshot, "value_at_risk"), risk.get("ytd_value_at_risk_pct")
            )
        ),
        # Captured on every risk call and discarded here until now. The
        # catalogue has advertised drawdown to callers all along.
        "drawdown_pct": _percent_text(
            _first_stated(_ytd_risk_metric(snapshot, "drawdown"), risk.get("ytd_drawdown_pct"))
        ),
        # Explicitly requested from lotus-risk, extracted, normalized, tested -
        # and then dropped at this boundary. Its basis travels in
        # `risk_methodology`, because the number alone is not interpretable.
        "expected_shortfall_pct": _percent_text(risk.get("ytd_expected_shortfall_pct")),
    }


def _risk_trend_section(snapshot: dict[str, Any]) -> dict[str, Any]:
    """ "Is this portfolio's risk changing?" - the agreed #255 contract.

    Absence of the captured block means the report did not order the risk
    section: no panel, not an empty one. Everything emitted is a source-owned
    fact: the series verbatim (a source gap is a visible gap), the window and
    frequency stated beside it, per-metric posture derived only from the
    source's own coverage facts, and NO trend statement unless lotus-risk
    states one (it does not today). A ready series needs at least two
    computed points - one point cannot state a trend, and drawing it flat
    would be derivation.
    """

    trend = _as_dict(snapshot.get("riskTrend"))
    if not trend:
        return {}
    request = _as_dict(trend.get("request"))
    requested_metrics = [str(metric) for metric in request.get("metrics") or []]
    supportability = _as_dict(trend.get("supportability"))
    window: dict[str, Any] = {
        "window_observations": _optional_int(request.get("window_observations")),
        "frequency": _optional_str(request.get("frequency")) or "daily",
    }
    if supportability.get("status") == "unavailable":
        notes = [note for note in supportability.get("notes") or [] if isinstance(note, dict)]
        return _risk_trend_refusal(window, requested_metrics, notes)

    results = _as_dict(trend.get("results"))
    period = _as_dict(results.get("YTD"))
    if not period:
        return _risk_trend_refusal(
            window,
            requested_metrics,
            [
                {
                    "code": "risk_trend_period_missing",
                    "message": "The source answered without the requested period.",
                }
            ],
        )
    window["period"] = {
        "name": "YTD",
        "start_date": _optional_str(period.get("start_date")),
        "end_date": _optional_str(period.get("end_date")),
    }
    quality_flags = [str(flag) for flag in period.get("quality_flags") or []]
    period_error = _optional_str(period.get("error"))
    if period_error:
        # A period the source marks in error is SAID, not drawn.
        return _risk_trend_refusal(
            window,
            requested_metrics,
            [{"code": "risk_trend_source_error", "message": period_error}],
            quality_flags=quality_flags,
        )

    unit_semantics = _as_dict(_as_dict(trend.get("metadata")).get("metric_unit_semantics"))
    window_results = [item for item in period.get("window_results") or [] if isinstance(item, dict)]
    window_result = next(
        (
            item
            for item in window_results
            if _optional_int(item.get("window_length")) == window["window_observations"]
        ),
        None,
    )
    benchmark_context = _as_dict(period.get("benchmark_context"))
    metrics_out: list[dict[str, Any]] = []
    for metric in requested_metrics:
        metrics_out.append(
            _risk_trend_metric(
                metric=metric,
                window_result=window_result,
                benchmark_context=benchmark_context,
                quality_flags=quality_flags,
                unit=_optional_str(unit_semantics.get(metric)),
            )
        )
    return {"window": window, "metrics": metrics_out}


def _risk_attribution_section(snapshot: dict[str, Any]) -> dict[str, Any]:
    """ "What risk did we take for the result?" - the #254 contract locked
    with Render 2026-09-04.

    Absence of the captured block means the section was not ordered: no
    panel. Every emitted fact is source-owned: the reconciliation triple
    (total, reconciled sum, residual - required together, residual presented
    never allocated away), contributors in source order with no ranking,
    quality flags verbatim, refusals in the source's voice, and the
    source-stated unit on ready sets. weight_average and percent_contribution
    are decimal fractions of one STRUCTURALLY - the contract defines them so
    forever; nothing here rescales.
    """

    attribution = _as_dict(snapshot.get("riskAttribution"))
    if not attribution:
        return {}
    request = _as_dict(attribution.get("request"))
    requested_types = [str(item) for item in request.get("attribution_types") or []]
    requested_metrics = [str(item) for item in request.get("metrics") or []]
    grouping = _optional_str(request.get("grouping_dimension")) or "SECTOR"
    requested_sets = list(zip(requested_types, requested_metrics, strict=False))
    supportability = _as_dict(attribution.get("supportability"))
    if supportability.get("status") == "unavailable":
        notes = [note for note in supportability.get("notes") or [] if isinstance(note, dict)]
        return {
            "sets": [
                _risk_attribution_refusal(set_key, grouping, notes=notes)
                for set_key in requested_sets
            ]
        }
    results = _as_dict(attribution.get("results"))
    period = _as_dict(results.get("YTD"))
    if not period:
        return {
            "sets": [
                _risk_attribution_refusal(
                    set_key,
                    grouping,
                    notes=[
                        {
                            "code": "risk_attribution_period_missing",
                            "message": "The source answered without the requested period.",
                        }
                    ],
                )
                for set_key in requested_sets
            ]
        }
    window = {
        "period": {
            "name": "YTD",
            "start_date": _optional_str(period.get("start_date")),
            "end_date": _optional_str(period.get("end_date")),
        }
    }
    period_error = _optional_str(period.get("error"))
    if period_error:
        return {
            "window": window,
            "sets": [
                _risk_attribution_refusal(
                    set_key,
                    grouping,
                    notes=[{"code": "risk_attribution_source_error", "message": period_error}],
                )
                for set_key in requested_sets
            ],
        }
    metadata = _as_dict(attribution.get("metadata"))
    unit_semantics = _as_dict(metadata.get("metric_unit_semantics"))
    benchmark_context = _as_dict(metadata.get("benchmark_context"))
    gate_reason = _optional_str(metadata.get("stateful_active_risk_gate_reason"))
    source_sets = {
        (
            str(item.get("attribution_type")),
            str(item.get("metric")),
            str(item.get("grouping_dimension")),
        ): item
        for item in period.get("attribution_sets") or []
        if isinstance(item, dict)
    }
    sets_out = [
        _risk_attribution_set(
            set_key,
            grouping,
            source_set=source_sets.get((set_key[0], set_key[1], grouping)),
            unit=_optional_str(unit_semantics.get(set_key[1])),
            benchmark_context=benchmark_context,
            gate_reason=gate_reason,
        )
        for set_key in requested_sets
    ]
    return {"window": window, "sets": sets_out}


def _risk_attribution_refusal(
    set_key: tuple[str, str],
    grouping: str,
    *,
    notes: list[dict[str, Any]],
    posture: str = "unavailable",
    quality_flags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "attribution_type": set_key[0],
        "metric": set_key[1],
        "grouping_dimension": grouping,
        "posture": posture,
        "notes": notes,
        "quality_flags": quality_flags or [],
    }


_ATTRIBUTION_CONTRIBUTOR_REQUIRED = ("group_key", "component_contribution", "percent_contribution")


def _stated_number(value: Any) -> bool:
    # Risk-attribution ratios, not monetary amounts.
    return isinstance(value, (int, float)) and not isinstance(value, bool)  # monetary-float-allow


def _attribution_contributors(rows: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Contributor rows in source order, or None when any row lacks the
    locked fields (group_key, component_contribution, percent_contribution).
    Optional fields appear only when the source stated them."""

    contributors: list[dict[str, Any]] = []
    for row in rows:
        group_key = row.get("group_key")
        if (
            not isinstance(group_key, str)
            or not group_key
            or not _stated_number(row.get("component_contribution"))
            or not _stated_number(row.get("percent_contribution"))
        ):
            return None
        contributor: dict[str, Any] = {
            "group_key": group_key,
            "group_label": _optional_str(row.get("group_label")) or group_key,
            "component_contribution": repr(row.get("component_contribution")),
            "percent_contribution": repr(row.get("percent_contribution")),
        }
        if _stated_number(row.get("marginal_contribution")):
            contributor["marginal_contribution"] = repr(row.get("marginal_contribution"))
        if _stated_number(row.get("weight_average")):
            contributor["weight_average"] = repr(row.get("weight_average"))
        contributors.append(contributor)
    return contributors


def _risk_attribution_set(
    set_key: tuple[str, str],
    grouping: str,
    *,
    source_set: dict[str, Any] | None,
    unit: str | None,
    benchmark_context: dict[str, Any],
    gate_reason: str | None,
) -> dict[str, Any]:
    attribution_type, _metric = set_key
    if attribution_type == "ACTIVE_RISK" and benchmark_context.get("reason") not in (
        None,
        "APPLIED",
    ):
        return _risk_attribution_refusal(
            set_key,
            grouping,
            notes=[
                {
                    "code": "benchmark_not_applied",
                    "message": str(benchmark_context.get("reason")),
                }
            ],
        )
    if source_set is None:
        return _risk_attribution_refusal(
            set_key,
            grouping,
            notes=[
                {
                    "code": "attribution_set_missing",
                    "message": "The source emitted no result for the requested set.",
                }
            ],
        )
    quality_flags = [str(flag) for flag in source_set.get("quality_flags") or []]
    triple = {name: source_set.get(name) for name in ("total_value", "reconciled_sum", "residual")}
    if any(not _stated_number(value) for value in triple.values()):
        return _risk_attribution_refusal(
            set_key,
            grouping,
            notes=[
                {
                    "code": "attribution_reconciliation_incomplete",
                    "message": (
                        "The source did not state the full total / reconciled sum / "
                        "residual triple; a decomposition without its reconciliation "
                        "is not drawn."
                    ),
                }
            ],
            quality_flags=quality_flags,
        )
    contributors_raw = [
        item for item in source_set.get("contributors") or [] if isinstance(item, dict)
    ]
    if not contributors_raw:
        return _risk_attribution_refusal(
            set_key,
            grouping,
            posture="empty",
            notes=[
                {
                    "code": "risk_attribution_no_contributors",
                    "message": (
                        gate_reason
                        if gate_reason and gate_reason != "none"
                        else "The source stated the set without contributor rows."
                    ),
                }
            ],
            quality_flags=quality_flags,
        )
    contributors = _attribution_contributors(contributors_raw)
    if contributors is None:
        # A partial contributor row makes the DECOMPOSITION unreliable, not
        # just the row: stated, never part-drawn.
        return _risk_attribution_refusal(
            set_key,
            grouping,
            notes=[
                {
                    "code": "attribution_contributors_incomplete",
                    "message": (
                        "The source stated a contributor row without its locked "
                        "fields; the decomposition is stated, not drawn."
                    ),
                }
            ],
            quality_flags=quality_flags,
        )
    ready: dict[str, Any] = {
        "attribution_type": set_key[0],
        "metric": set_key[1],
        "grouping_dimension": grouping,
        "posture": "ready",
        "total_value": repr(triple["total_value"]),
        "reconciled_sum": repr(triple["reconciled_sum"]),
        "residual": repr(triple["residual"]),
        "contributors": contributors,
        "quality_flags": quality_flags,
    }
    if unit:
        ready["unit"] = unit
    return ready


def _risk_trend_refusal(
    window: dict[str, Any],
    requested_metrics: list[str],
    notes: list[dict[str, Any]],
    *,
    quality_flags: list[str] | None = None,
) -> dict[str, Any]:
    """Every requested metric refused for the same stated reason - the page
    says why, never draws."""

    metric_entry: dict[str, Any] = {"posture": "unavailable", "notes": notes}
    if quality_flags is not None:
        metric_entry["quality_flags"] = quality_flags
    return {
        "window": window,
        "metrics": [{"metric": metric, **metric_entry} for metric in requested_metrics],
    }


_BENCHMARK_DEPENDENT_ROLLING_METRICS = frozenset({"ROLLING_BETA", "ROLLING_TRACKING_ERROR"})


def _risk_trend_metric(
    *,
    metric: str,
    window_result: dict[str, Any] | None,
    benchmark_context: dict[str, Any],
    quality_flags: list[str],
    unit: str | None,
) -> dict[str, Any]:
    if metric in _BENCHMARK_DEPENDENT_ROLLING_METRICS and benchmark_context.get("reason") not in (
        None,
        "APPLIED",
    ):
        # The #241 voice: a benchmark-relative series on a benchmark the
        # source could not apply is stated unavailable with the source's own
        # reason - never invisible, never drawn from partial alignment.
        return {
            "metric": metric,
            "posture": "unavailable",
            "notes": [
                {
                    "code": "benchmark_not_applied",
                    "message": str(benchmark_context.get("reason")),
                }
            ],
            "quality_flags": quality_flags,
        }
    if window_result is None:
        return {
            "metric": metric,
            "posture": "unavailable",
            "notes": [
                {
                    "code": "risk_trend_window_missing",
                    "message": "The source emitted no result for the requested window.",
                }
            ],
            "quality_flags": quality_flags,
        }
    context = _as_dict(window_result.get("metric_series_context"))
    if not context.get("included"):
        return {
            "metric": metric,
            "posture": "empty",
            "notes": [
                {
                    "code": "risk_trend_series_not_included",
                    "message": str(context.get("reason") or "NO_METRIC_SERIES"),
                }
            ],
            "quality_flags": quality_flags,
        }
    series: list[dict[str, Any]] = []
    computed_points = 0
    for point in window_result.get("metric_series") or []:
        if not isinstance(point, dict):
            continue
        date_text = _optional_str(point.get("date"))
        if not date_text:
            continue
        values = _as_dict(point.get("metric_values"))
        value = values.get(metric)
        if value is None:
            # The source stated an observation slot here without computing
            # this metric. That fact is PRESERVED, never dropped: the point
            # carries value null plus the posture naming why, and only ever
            # both together. No dates are generated and no calendar is
            # classified - lotus-risk owns cadence truth; how an explicit
            # gap is displayed is lotus-render's decision.
            series.append({"date": date_text, "value": None, "point_posture": "not_computed"})
            continue
        if isinstance(value, bool) or isinstance(value, str):
            continue
        # Not monetary: a rolling risk ratio forwarded at the precision the
        # source's JSON number round-trips to.
        series.append({"date": date_text, "value": repr(value)})
        computed_points += 1
    if computed_points < 2:
        # One point cannot state a trend; a "ready" claim over it would be
        # derivation. Fail-visible per the agreed contract.
        return {
            "metric": metric,
            "posture": "unavailable",
            "notes": [
                {
                    "code": "series_insufficient_for_trend",
                    "message": (
                        f"The source emitted {computed_points} computed point(s); a trend "
                        "needs at least two."
                    ),
                }
            ],
            "quality_flags": quality_flags,
        }
    ready: dict[str, Any] = {
        "metric": metric,
        "posture": "ready",
        "series": series,
        "quality_flags": quality_flags,
    }
    if unit:
        # The source's own unit statement, forwarded verbatim: decimal_ratio
        # values are decimal fractions of one, unitless values read at face
        # value. Absent when the source stated none - never guessed, so the
        # renderer refuses to print an ambiguous number.
        ready["unit"] = unit
    return ready


def _governance_summary_section(
    *,
    snapshot: dict[str, Any],
    evidence: dict[str, Any],
    trust_metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "source_services": [
            str(item)
            for item in evidence.get("source_services", [])
            if isinstance(item, str) and item.strip()
        ],
        "completeness_status": _optional_str(trust_metadata.get("completeness_status"))
        or "unknown",
        "data_quality_status": _optional_str(trust_metadata.get("data_quality_status"))
        or "unknown",
        "readiness_status": _optional_str(_as_dict(snapshot.get("readiness")).get("status"))
        or "unknown",
    }


def template_contract_mismatch(
    package: dict[str, Any], render_response: dict[str, Any]
) -> str | None:
    """A stated failure when Render's response disagrees with the ordered template.

    Report ordered exactly one governed (template_id, template_version) - the
    acceptance fact the document_reference binds. The response must state the
    same pair: silence cannot prove equality, and a different pair means the
    artifact is not the document this job accepted. Returns the failure
    message, or None when the contract holds.
    """

    ordered_id = str(package.get("template_id") or "")
    ordered_version = str(package.get("template_version") or "")
    stated_id = _optional_str(render_response.get("template_id"))
    stated_version = _optional_str(render_response.get("template_version"))
    if stated_id == ordered_id and stated_version == ordered_version:
        return None
    return (
        "RENDER_TEMPLATE_CONTRACT_MISMATCH: ordered "
        f"{ordered_id}/{ordered_version}, response stated "
        f"{stated_id or 'nothing'}/{stated_version or 'nothing'}; refusing to "
        "record an artifact this job never ordered."
    )


def _accepted_axis(job: ReportJobLedgerRecord, axis: str) -> str | None:
    contract = job.accepted_document_contract or {}
    value = contract.get(axis)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _job_report_data_contract(job: ReportJobLedgerRecord) -> str:
    """The report-data contract the job was ACCEPTED under (report#283
    finding 6), FAIL-CLOSED against composition drift: this deployment's
    composers emit exactly one shape per family, so an accepted version
    this deployment no longer composes must refuse - labelling a new-shaped
    payload with an old version would hand Render a lie. Regeneration
    under the current contract is the governed remedy. A legacy job that
    never persisted the axis resolves today's definition with no
    accepted-contract claim."""

    current = resolve_report_data_contract(job.report_type)
    accepted = _accepted_axis(job, "report_data_contract_version")
    if accepted is not None and accepted != current:
        raise ValueError(
            "RENDER_PACKAGE_ACCEPTED_CONTRACT_UNSUPPORTED: job "
            f"{job.job_id} was accepted under report-data contract "
            f"{accepted}, but this deployment composes {current}; refusing "
            "to mislabel the payload - regenerate under the current "
            "contract to produce a replacement document."
        )
    return accepted or current


def _job_render_package_version(job: ReportJobLedgerRecord) -> str:
    """The envelope version, fail-closed exactly like the report-data
    contract: the envelope structure is bound to this deployment's code."""

    accepted = _accepted_axis(job, "render_package_version")
    if accepted is not None and accepted != GOVERNED_RENDER_PACKAGE_VERSION:
        raise ValueError(
            "RENDER_PACKAGE_ACCEPTED_CONTRACT_UNSUPPORTED: job "
            f"{job.job_id} was accepted under envelope "
            f"{accepted}, but this deployment builds "
            f"{GOVERNED_RENDER_PACKAGE_VERSION}; refusing to mislabel the "
            "package - regenerate under the current contract."
        )
    return accepted or GOVERNED_RENDER_PACKAGE_VERSION


def _job_disclosure_baseline(job: ReportJobLedgerRecord) -> str:
    return (
        _accepted_axis(job, "standard_disclosure_ref")
        or resolve_report_family(job.report_type).standard_disclosure_ref
    )


def _job_template_identity(job: ReportJobLedgerRecord) -> tuple[str, str]:
    """The governed template id/version persisted on the job at acceptance.

    Fails closed: the envelope never invents a template version. A PDF job
    without a persisted template identity is a defect (pre-backfill data or a
    creation-path regression), not something rendering may paper over with a
    default - a defaulted version could silently rebuild an accepted document
    on a presentation contract it never agreed to.
    """

    template_id = (job.render_template_id or "").strip()
    template_version = (job.render_template_version or "").strip()
    if not template_id or not template_version:
        raise ValueError(
            "RENDER_PACKAGE_TEMPLATE_IDENTITY_REQUIRED: governed rendering "
            f"requires the template id/version persisted on job {job.job_id} "
            "at acceptance; refusing to invent a presentation contract."
        )
    return template_id, template_version


def _render_package_envelope(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
    snapshot_id: str,
    report_data_contract_version: str,
    report_data: dict[str, Any],
    lineage_refs: list[str],
    disclosure_refs: list[str],
    report_revision_id: str | None = None,
) -> dict[str, Any]:
    """The envelope every render package shares; only the typed content varies.

    One definition instead of four verbatim copies, so an envelope change (a new
    locale policy, a context field) cannot land in three report families and miss
    the fourth.
    """

    # The identity printed into a governed document must be exactly the
    # durable Report snapshot identity later recorded in Archive lineage. It
    # therefore arrives from the ReportInputSnapshotRecord - the caller's
    # durable fact - never rediscovered from the payload (which does not
    # carry it in production) and never synthesised: a document wearing
    # "snapshot-for-<job>" while Archive records rsnap_... is two names for
    # one piece of evidence, disagreeing on the page that matters most.
    if not (isinstance(snapshot_id, str) and snapshot_id.strip()):
        raise ValueError(
            "RENDER_PACKAGE_SNAPSHOT_IDENTITY_REQUIRED: governed rendering "
            "requires the durable snapshot id; refusing to mint a document "
            "identity for unidentified evidence."
        )
    template_id, template_version = _job_template_identity(job)
    return {
        "render_package_version": _job_render_package_version(job),
        "render_job_id": render_job_id,
        "report_job_id": job.job_id,
        "snapshot_id": snapshot_id,
        "report_type": job.report_type,
        "report_data_contract_version": report_data_contract_version,
        "template_id": template_id,
        "template_version": template_version,
        "locale": _accepted_axis(job, "locale") or GOVERNED_LOCALE,
        "brand_variant": _accepted_axis(job, "brand_variant") or GOVERNED_BRAND_VARIANT,
        "output_format": "pdf",
        "render_context": {
            "timezone": "Asia/Singapore",
            # The canonical revision identity of the facts this document
            # presents, from the durable snapshot record - the same identity
            # Archive stores. Absent (never synthesised) for snapshots
            # captured before revision identity existed.
            **({"report_revision_id": report_revision_id} if report_revision_id else {}),
            # The governed identity every client document carries in its
            # footer. Minted here - in the ONE envelope all four families
            # share - from the financial question (job, snapshot, template),
            # never from per-attempt values: a rerender of the same snapshot
            # converges on the same reference, a regenerate or corrected
            # template carries its own.
            "document_reference": mint_document_reference(
                report_job_id=job.job_id,
                snapshot_id=snapshot_id,
                template_id=template_id,
                template_version=template_version,
            ),
            # The custody facts only Report owns, passed to Archive verbatim
            # by lotus-render's handoff (its build_archive_metadata). Render
            # overlays identity, provenance, and the declared digest LAST, so
            # nothing stated here can override what Render actually did -
            # which is also why none of those overlaid fields appear here.
            "archive": _archive_custody_block(
                job=job, snapshot=snapshot, report_revision_id=report_revision_id
            ),
        },
        "report_data": report_data,
        "lineage_refs": lineage_refs,
        "disclosure_refs": disclosure_refs,
        "requested_by": job.triggered_by,
        "correlation_id": job.correlation_id,
        "trace_id": job.trace_id,
    }


def _advisor_commentary_archive_summary(
    snapshot_payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Archive metadata keeps the accepted brief's audit identity for a
    rendered ADVISOR_COMMENTARY section (issue #166 acceptance 4)."""

    package = _as_dict(snapshot_payload.get("advisor_commentary_package"))
    if not package or package.get("status") != "included":
        return None
    review = _as_dict(package.get("review"))
    return {
        "run_id": _optional_str(package.get("run_id")) or "not_available",
        "request_id": _optional_str(package.get("request_id")) or "not_available",
        "reviewed_by": _optional_str(review.get("reviewed_by")) or "not_available",
        "reviewed_at": _optional_str(review.get("reviewed_at")) or "not_available",
        "content_hash": _optional_str(package.get("content_hash")) or "not_available",
        "schema_id": _optional_str(package.get("schema_id")) or "not_available",
        "included_in_render": True,
    }


def _advisor_proposal_memo_archive_summary(
    snapshot_payload: dict[str, Any],
) -> dict[str, Any] | None:
    package = _as_dict(snapshot_payload.get("proposal_memo_package"))
    if not package:
        return None
    review = _as_dict(package.get("review"))
    sections = [section for section in package.get("sections", []) if isinstance(section, dict)]
    return {
        "memo_id": _optional_str(package.get("memo_id")) or "not_available",
        "proposal_id": _optional_str(package.get("proposal_id")) or "not_available",
        "proposal_version_no": _optional_int(package.get("proposal_version_no")) or 0,
        "review_event_id": _optional_str(review.get("review_event_id")) or "not_available",
        "review_action": _optional_str(review.get("review_action")) or "not_available",
        "client_ready_status": _optional_str(package.get("client_ready_publication")) or "BLOCKED",
        "memo_hash": _optional_str(package.get("memo_hash")) or "not_available",
        "source_input_hash": _optional_str(package.get("source_input_hash")) or "not_available",
        "section_count": len(sections),
        "blocked_section_count": sum(
            1 for section in sections if section.get("status") == "BLOCKED"
        ),
        "included_in_render": True,
    }


def _archive_custody_block(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    report_revision_id: str | None = None,
) -> dict[str, Any]:
    """Report-owned custody metadata for the render#120 archive handoff.

    Exactly the ArchiveDocumentInput fields Report is the authority on:
    request lineage, portfolio scope, reporting period, classification,
    residency, retention, and the report-family audit summaries. Fields the
    delivering authority owns (archive_request_id, document_reference,
    declared_artifact_sha256, render identity/provenance, created_by_*) are
    deliberately absent - lotus-render overlays them and would discard any
    value stated here.
    """

    review_period = _as_dict(snapshot.get("reviewPeriod"))
    identity = _as_dict(_as_dict(snapshot.get("clientProfile")).get("identity"))
    portfolio_ids = job.portfolio_scope.get("portfolio_ids")
    portfolio_id = (
        str(portfolio_ids[0])
        if isinstance(portfolio_ids, list) and portfolio_ids
        else "portfolio-not-available"
    )
    custody: dict[str, Any] = {
        "report_request_id": job.request_id,
        "portfolio_scope": json.dumps(job.portfolio_scope, sort_keys=True, separators=(",", ":")),
        "portfolio_id": portfolio_id,
        "as_of_date": job.as_of_date.isoformat(),
        "reporting_period_start": _date_text(
            review_period.get("start_date")
            or review_period.get("period_start")
            or date(job.as_of_date.year, 1, 1)
        ),
        "reporting_period_end": _date_text(
            review_period.get("end_date") or review_period.get("period_end") or job.as_of_date
        ),
        "frequency": _optional_str(review_period.get("frequency")) or "ad_hoc",
        "classification": "confidential",
        "region": job.region,
        "tenant_id": job.tenant_id,
        "retention_start_date": job.as_of_date.isoformat(),
    }
    if report_revision_id:
        # Archive stores this opaque reference verbatim (archive migration
        # 011); a pre-identity snapshot states nothing rather than a
        # fabricated identity.
        custody["report_revision_id"] = report_revision_id
    client_reference = _optional_str(identity.get("client_reference")) or _optional_str(
        identity.get("client_id")
    )
    if client_reference:
        custody["client_reference"] = client_reference
    retention_policy_id = _optional_str(job.options.get("retention_policy_id"))
    if retention_policy_id:
        custody["retention_policy_id"] = retention_policy_id
    retain_until_date = _optional_str(job.options.get("retain_until_date"))
    if retain_until_date:
        custody["retain_until_date"] = retain_until_date
    advisor_memo = _advisor_proposal_memo_archive_summary(snapshot)
    if advisor_memo is not None:
        custody["advisor_proposal_memo"] = advisor_memo
    advisor_commentary = _advisor_commentary_archive_summary(snapshot)
    if advisor_commentary is not None:
        custody["advisor_commentary"] = advisor_commentary
    return custody


def _date_text(value: object) -> str:
    if isinstance(value, date):
        return str(value.isoformat())
    text = _optional_str(value)
    if text:
        return str(text)
    raise ValueError("date value is required")


def _validate_dpm_common_snapshot(
    *,
    snapshot: dict[str, Any],
    report_type: str,
    identity_field: str,
    content_hash_field: str,
) -> None:
    _require_dpm_value(snapshot, report_type, identity_field)
    _require_dpm_sha256(snapshot, report_type, "content_hash")
    _require_dpm_sha256(snapshot, report_type, content_hash_field)
    _require_dpm_value(snapshot, report_type, "redaction_policy")
    _require_dpm_value(snapshot, report_type, "retention_policy")
    evidence_ref = _as_dict(snapshot.get("evidence_ref"))
    if not evidence_ref:
        raise ValueError(f"{report_type}.evidence_ref is required")
    evidence_hash = _optional_str(evidence_ref.get("content_hash"))
    if not evidence_hash or not evidence_hash.startswith("sha256:"):
        raise ValueError(f"{report_type}.evidence_ref.content_hash must use sha256 lineage")


def _require_dpm_value(snapshot: dict[str, Any], report_type: str, field_name: str) -> str:
    value = _optional_str(snapshot.get(field_name))
    if not value or value == "not_available":
        raise ValueError(f"{report_type}.{field_name} is required")
    return value


def _require_dpm_sha256(snapshot: dict[str, Any], report_type: str, field_name: str) -> str:
    value = _require_dpm_value(snapshot, report_type, field_name)
    if not value.startswith("sha256:"):
        raise ValueError(f"{report_type}.{field_name} must use sha256 lineage")
    return value


def _require_dpm_list(snapshot: dict[str, Any], report_type: str, field_name: str) -> list[Any]:
    value = snapshot.get(field_name)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{report_type}.{field_name} is required")
    return value


def _build_proof_pack_render_package(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
    snapshot_id: str,
    report_revision_id: str | None = None,
) -> dict[str, Any]:
    _validate_dpm_common_snapshot(
        snapshot=snapshot,
        report_type="proof_pack_report_input",
        identity_field="proof_pack_id",
        content_hash_field="proof_pack_content_hash",
    )
    _require_dpm_value(snapshot, "proof_pack_report_input", "portfolio_id")
    _require_dpm_list(snapshot, "proof_pack_report_input", "sections")
    sections = [
        {
            "section_id": _optional_str(item.get("section_id")) or "not_available",
            "section_type": _optional_str(item.get("section_type")) or "not_available",
            "state": _optional_str(item.get("state")) or "not_available",
            "title": _optional_str(item.get("title")) or "Not available",
            "summary": _optional_str(item.get("summary")) or "No section summary supplied.",
            "reason_codes": _string_list(item.get("reason_codes")),
            "content_hash": _require_dpm_sha256(
                item,
                "proof_pack_report_input.sections",
                "content_hash",
            ),
        }
        for item in snapshot.get("sections", [])
        if isinstance(item, dict)
    ]
    portfolio_id = _optional_str(snapshot.get("portfolio_id")) or "Portfolio"
    portfolio_memory = _portfolio_memory_context(snapshot)
    report_data = {
        "title": _optional_str(snapshot.get("report_title"))
        or f"Pre-Trade Proof Pack - {portfolio_id}",
        "portfolio_id": _optional_str(snapshot.get("portfolio_id")) or "not_available",
        "proof_pack_id": _optional_str(snapshot.get("proof_pack_id")) or "not_available",
        "mandate_id": _optional_str(snapshot.get("mandate_id")) or "not_available",
        "as_of_date": _optional_str(snapshot.get("as_of_date")) or job.as_of_date.isoformat(),
        "state": _optional_str(snapshot.get("state")) or "not_available",
        "decision_summary": _as_dict(snapshot.get("decision_summary")),
        "supportability": _as_dict(snapshot.get("supportability")),
        "sections": sections,
        "source_hashes": _as_dict(snapshot.get("source_hashes")),
        "content_hash": _require_dpm_sha256(
            snapshot,
            "proof_pack_report_input",
            "content_hash",
        ),
        "proof_pack_content_hash": _require_dpm_sha256(
            snapshot,
            "proof_pack_report_input",
            "proof_pack_content_hash",
        ),
        "redaction_policy": _optional_str(snapshot.get("redaction_policy")) or "NO_RAW_PAYLOADS",
        "portfolio_memory": portfolio_memory,
    }
    return _render_package_envelope(
        job=job,
        snapshot=snapshot,
        render_job_id=render_job_id,
        snapshot_id=snapshot_id,
        report_revision_id=report_revision_id,
        report_data_contract_version=_job_report_data_contract(job),
        report_data=report_data,
        lineage_refs=_dpm_lineage_refs(
            job.job_id,
            report_data["proof_pack_id"],
            report_data["content_hash"],
            portfolio_memory,
        ),
        disclosure_refs=[_job_disclosure_baseline(job)],
    )


def _build_outcome_review_render_package(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
    snapshot_id: str,
    report_revision_id: str | None = None,
) -> dict[str, Any]:
    _validate_dpm_common_snapshot(
        snapshot=snapshot,
        report_type="outcome_report_input",
        identity_field="outcome_review_id",
        content_hash_field="outcome_review_content_hash",
    )
    _require_dpm_value(snapshot, "outcome_report_input", "portfolio_id")
    _require_dpm_list(snapshot, "outcome_report_input", "dimensions")
    _require_dpm_list(snapshot, "outcome_report_input", "source_lineage")
    review_window = _as_dict(snapshot.get("review_window"))
    dimensions = [
        {
            "dimension": _optional_str(item.get("dimension")) or "not_available",
            "state": _optional_str(item.get("state")) or "not_available",
            "reason_code": _optional_str(item.get("reason_code")) or "not_available",
            "expected": _optional_str(item.get("expected")) or "not_available",
            "realized": _optional_str(item.get("realized")) or "not_available",
            "variance": _optional_str(item.get("variance")) or "not_available",
            "explanation": _optional_str(item.get("explanation")) or "No explanation supplied.",
        }
        for item in snapshot.get("dimensions", [])
        if isinstance(item, dict)
    ]
    source_services = sorted(
        {
            _optional_str(item.get("source_system")) or "lotus-manage"
            for item in snapshot.get("source_lineage", [])
            if isinstance(item, dict)
        }
        or {"lotus-manage"}
    )
    portfolio_id = _optional_str(snapshot.get("portfolio_id")) or "Portfolio"
    portfolio_memory = _portfolio_memory_context(snapshot)
    report_data = {
        "title": _optional_str(snapshot.get("report_title"))
        or f"Post-Trade Outcome Review - {portfolio_id}",
        "portfolio_id": _optional_str(snapshot.get("portfolio_id")) or "not_available",
        "outcome_review_id": _optional_str(snapshot.get("outcome_review_id")) or "not_available",
        "proof_pack_id": _optional_str(snapshot.get("proof_pack_id")) or "not_available",
        "rebalance_run_id": _optional_str(snapshot.get("rebalance_run_id")) or "not_available",
        "wave_id": _optional_str(snapshot.get("wave_id")) or "not_available",
        "state": _optional_str(snapshot.get("state")) or "not_available",
        "overall_outcome": _optional_str(snapshot.get("overall_outcome"))
        or "Outcome summary was not supplied.",
        "review_window_start": _optional_str(review_window.get("start_date"))
        or _optional_str(review_window.get("period_start"))
        or "not_available",
        "review_window_end": _optional_str(review_window.get("end_date"))
        or _optional_str(review_window.get("period_end"))
        or job.as_of_date.isoformat(),
        "dimensions": dimensions,
        "source_services": source_services,
        "source_hashes": _as_dict(snapshot.get("source_hashes")),
        "section_hashes": _as_dict(snapshot.get("section_hashes")),
        "content_hash": _require_dpm_sha256(
            snapshot,
            "outcome_report_input",
            "content_hash",
        ),
        "outcome_review_content_hash": _require_dpm_sha256(
            snapshot,
            "outcome_report_input",
            "outcome_review_content_hash",
        ),
        "redaction_policy": _optional_str(snapshot.get("redaction_policy")) or "NO_RAW_PAYLOADS",
        "portfolio_memory": portfolio_memory,
    }
    return _render_package_envelope(
        job=job,
        snapshot=snapshot,
        render_job_id=render_job_id,
        snapshot_id=snapshot_id,
        report_revision_id=report_revision_id,
        report_data_contract_version=_job_report_data_contract(job),
        report_data=report_data,
        lineage_refs=_dpm_lineage_refs(
            job.job_id,
            report_data["outcome_review_id"],
            report_data["content_hash"],
            portfolio_memory,
        ),
        disclosure_refs=[_job_disclosure_baseline(job)],
    )


def _build_wave_render_package(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
    snapshot_id: str,
    report_revision_id: str | None = None,
) -> dict[str, Any]:
    _validate_dpm_common_snapshot(
        snapshot=snapshot,
        report_type="wave_report_input",
        identity_field="wave_id",
        content_hash_field="wave_content_hash",
    )
    _require_dpm_value(snapshot, "wave_report_input", "wave_state")
    _require_dpm_list(snapshot, "wave_report_input", "items")
    _require_dpm_list(snapshot, "wave_report_input", "source_refs")
    items = [
        {
            "wave_item_id": _optional_str(item.get("wave_item_id")) or "not_available",
            "portfolio_id": _optional_str(item.get("portfolio_id")) or "not_available",
            "state": _optional_str(item.get("state")) or "not_available",
            "mandate_id": _optional_str(item.get("mandate_id")) or "not_available",
            "selected_alternative_id": _optional_str(item.get("selected_alternative_id"))
            or "not_available",
            "proof_pack_id": _optional_str(item.get("proof_pack_id")) or "not_available",
            "proof_pack_state": _optional_str(item.get("proof_pack_state")) or "not_available",
            "reason_codes": _string_list(item.get("reason_codes")),
        }
        for item in snapshot.get("items", [])
        if isinstance(item, dict)
    ]
    events = [
        {
            "event_type": _optional_str(item.get("event_type")) or "not_available",
            "to_state": _optional_str(item.get("to_state")) or "not_available",
            "actor_id": _optional_str(item.get("actor_id")) or "not_available",
            "reason_code": _optional_str(item.get("reason_code")) or "not_available",
            "created_at": _optional_str(item.get("created_at")) or "not_available",
        }
        for item in snapshot.get("events", [])[-8:]
        if isinstance(item, dict)
    ]
    portfolio_memory = _portfolio_memory_context(snapshot)
    report_data = {
        "title": _optional_str(snapshot.get("report_title"))
        or f"Rebalance Wave Evidence - {snapshot.get('wave_id', 'not_available')}",
        "wave_id": _optional_str(snapshot.get("wave_id")) or "not_available",
        "wave_state": _optional_str(snapshot.get("wave_state")) or "not_available",
        "trigger_type": _optional_str(snapshot.get("trigger_type")) or "not_available",
        "trigger_id": _optional_str(snapshot.get("trigger_id")) or "not_available",
        "trigger_rationale": _optional_str(snapshot.get("trigger_rationale"))
        or "No trigger rationale supplied.",
        "as_of_date": _optional_str(snapshot.get("as_of_date")) or job.as_of_date.isoformat(),
        "aggregate_metrics": _as_dict(snapshot.get("aggregate_metrics")),
        "supportability": _as_dict(snapshot.get("supportability")),
        "proof_pack_posture": _as_dict(snapshot.get("proof_pack_posture")),
        "items": items,
        "events": events,
        "handoff_count": len(snapshot.get("handoff_refs", []))
        if isinstance(snapshot.get("handoff_refs"), list)
        else 0,
        "external_execution_claimed": bool(snapshot.get("external_execution_claimed")),
        "content_hash": _require_dpm_sha256(
            snapshot,
            "wave_report_input",
            "content_hash",
        ),
        "wave_content_hash": _require_dpm_sha256(
            snapshot,
            "wave_report_input",
            "wave_content_hash",
        ),
        "redaction_policy": _optional_str(snapshot.get("redaction_policy")) or "NO_RAW_PAYLOADS",
        "portfolio_memory": portfolio_memory,
    }
    return _render_package_envelope(
        job=job,
        snapshot=snapshot,
        render_job_id=render_job_id,
        snapshot_id=snapshot_id,
        report_revision_id=report_revision_id,
        report_data_contract_version=_job_report_data_contract(job),
        report_data=report_data,
        lineage_refs=_dpm_lineage_refs(
            job.job_id,
            report_data["wave_id"],
            report_data["content_hash"],
            portfolio_memory,
        ),
        disclosure_refs=[_job_disclosure_baseline(job)],
    )


def _portfolio_memory_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    context = _as_dict(snapshot.get("portfolio_memory_context"))
    if not context:
        return _empty_portfolio_memory_context()

    raw_event_refs = context.get("event_refs")
    if not isinstance(raw_event_refs, list):
        raw_event_refs = []
    event_refs = [
        _portfolio_memory_event_ref(item) for item in raw_event_refs[:12] if isinstance(item, dict)
    ]
    event_ref_limit = _optional_int(context.get("event_ref_limit"))
    event_refs_returned = _optional_int(context.get("event_refs_returned"))
    event_refs_omitted = _optional_int(context.get("event_refs_omitted"))
    event_refs_truncated = _optional_bool(context.get("event_refs_truncated"))
    return {
        "status": "supplied",
        "portfolio_id": _optional_str(context.get("portfolio_id")) or "not_available",
        "supportability_state": _optional_str(context.get("supportability_state"))
        or "not_available",
        "event_count": _optional_int(context.get("event_count")) or len(event_refs),
        "source_systems": _string_list(context.get("source_systems")),
        "reason_codes": _string_list(context.get("reason_codes")),
        "content_hash": _optional_str(context.get("content_hash")) or "not_available",
        "context_content_hash": _optional_str(context.get("context_content_hash"))
        or "not_available",
        "support_boundary": _optional_str(context.get("support_boundary")) or "not_available",
        "event_ref_limit": event_ref_limit if event_ref_limit is not None else 0,
        "event_ref_selection_policy": _optional_str(context.get("event_ref_selection_policy"))
        or "not_available",
        "event_refs_returned": (
            event_refs_returned if event_refs_returned is not None else len(event_refs)
        ),
        "event_refs_omitted": event_refs_omitted if event_refs_omitted is not None else 0,
        "event_refs_truncated": (
            event_refs_truncated if event_refs_truncated is not None else False
        ),
        "governance_policy": _as_dict(context.get("governance_policy")),
        "event_refs": event_refs,
    }


def _empty_portfolio_memory_context() -> dict[str, Any]:
    return {
        "status": "not_supplied",
        "event_count": 0,
        "content_hash": "not_available",
        "context_content_hash": "not_available",
        "support_boundary": "not_available",
        "event_ref_limit": 0,
        "event_ref_selection_policy": "not_available",
        "event_refs_returned": 0,
        "event_refs_omitted": 0,
        "event_refs_truncated": False,
        "event_refs": [],
        "governance_policy": {},
    }


def _portfolio_memory_event_ref(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_identity": _optional_str(item.get("event_identity")) or "not_available",
        "event_type": _optional_str(item.get("event_type")) or "not_available",
        "source_system": _optional_str(item.get("source_system")) or "not_available",
        "source_type": _optional_str(item.get("source_type")) or "not_available",
        "source_id": _optional_str(item.get("source_id")) or "not_available",
        "content_hash": _optional_str(item.get("content_hash")) or "not_available",
        "retention_policy": _optional_str(item.get("retention_policy")) or "not_available",
        "redaction_policy": _optional_str(item.get("redaction_policy")) or "not_available",
        "audit_policy": _optional_str(item.get("audit_policy")) or "not_available",
        "access_classification": _optional_str(item.get("access_classification"))
        or "not_available",
        "event_time": _optional_str(item.get("event_time")) or "not_available",
        "event_ref_selection_rank": _optional_int(item.get("event_ref_selection_rank")),
        "manage_lookup_id": _optional_str(
            item.get("manage_lookup_id")
            or item.get("lookup_id")
            or item.get("event_lookup_id")
            or item.get("portfolio_memory_event_lookup_id")
        )
        or "not_available",
    }


def _dpm_lineage_refs(
    job_id: str,
    source_id: object,
    content_hash: object,
    portfolio_memory: dict[str, Any],
) -> list[str]:
    refs = [
        job_id,
        _optional_str(source_id) or "not_available",
        _optional_str(content_hash) or "not_available",
    ]
    memory_hash = _optional_str(portfolio_memory.get("content_hash"))
    if portfolio_memory.get("status") == "supplied" and memory_hash:
        refs.append(memory_hash)
    return refs


def _summary_paragraph(snapshot: dict[str, Any]) -> str:
    executive_summary = next(
        (
            _optional_str(item.get("summary"))
            for item in snapshot.get("reviewObservations", [])
            if isinstance(item, dict) and _optional_str(item.get("summary"))
        ),
        None,
    )
    if executive_summary:
        return executive_summary
    readiness = _as_dict(snapshot.get("readiness"))
    status = _optional_str(readiness.get("status")) or "ready"
    return (
        "Portfolio review data capture completed in lotus-report with readiness "
        f"{status} for the requested as-of date."
    )


def _review_observations(
    snapshot: dict[str, Any],
    performance: dict[str, Any],
    risk: dict[str, Any],
    holdings: dict[str, Any],
) -> list[str]:
    snapshot_observations = [
        text
        for text in (
            _optional_str(item.get("summary"))
            for item in snapshot.get("reviewObservations", [])
            if isinstance(item, dict)
        )
        if text
    ]
    if snapshot_observations:
        return snapshot_observations
    return [
        text
        for text in [
            _performance_observation(performance),
            _risk_observation(risk),
            _holding_observation(holdings),
        ]
        if text
    ]


def _performance_observation(performance: dict[str, Any]) -> str | None:
    contributor = _as_dict(performance.get("largest_positive_contributor"))
    name = _optional_str(contributor.get("security_name")) or _optional_str(
        contributor.get("security_id")
    )
    contribution = _optional_decimal(contributor.get("ytd_contribution_pct"))
    if name and contribution is not None:
        return (
            f"{name} was the largest positive contributor at "
            f"{contribution.quantize(Decimal('0.01'))}% YTD contribution."
        )
    benchmark_status = _optional_str(performance.get("benchmark_comparison_status"))
    if benchmark_status:
        return f"Benchmark comparison status is {benchmark_status} in the governed report snapshot."
    return None


def _risk_observation(risk: dict[str, Any]) -> str | None:
    volatility = _optional_decimal(risk.get("ytd_volatility_pct"))
    beta = _optional_decimal(risk.get("ytd_beta"))
    if volatility is not None and beta is not None:
        return (
            f"YTD volatility is {volatility.quantize(Decimal('0.01'))}% "
            f"and beta is {beta.quantize(Decimal('0.01'))}."
        )
    return None


def _holding_observation(holdings: dict[str, Any]) -> str | None:
    count = _optional_int(holdings.get("position_count"))
    if count is not None:
        return f"The report includes {count} sourced portfolio positions."
    return None


def _portfolio_name(job: ReportJobLedgerRecord, snapshot: dict[str, Any]) -> str:
    return (
        _optional_str(snapshot.get("portfolio_name"))
        or _optional_str(snapshot.get("portfolioName"))
        or job.portfolio_scope.get("portfolio_ids", ["Portfolio"])[0]
    )


#: The fee basis of every performance figure this package presents, and the
#: snapshot keys that carry it. Report presents returns NET of fees, and the
#: basis is published rather than left encoded in a field name: nothing fails
#: when a renderer reads `net_return_pct` into a column headed "Portfolio",
#: which is exactly what happened, and net versus gross changes what every
#: number in the most-read table means to the person paying the fees.
#:
#: The keys live beside the basis so the two cannot disagree. Publishing "NET"
#: while reading a gross field would be a confident wrong statement about
#: money, which is worse than saying nothing.
#:
#: Deliberately NOT sourced from `performance.methodology.performance_basis`.
#: That is the static string "NET_AND_GROSS_WHERE_AVAILABLE", set without
#: reference to what was computed - a declared posture, not a measured one.
PRESENTED_RETURN_BASIS = "NET"
_PRESENTED_CUMULATIVE_RETURN_KEY = "net_cumulative_return"
_PRESENTED_ANNUALIZED_RETURN_KEY = "net_annualized_return"


#: The period the fee-drag figure describes: the same period the document's
#: other basis statements present.
_FEE_DRAG_PERIOD = "YTD"


def _performance_basis_section(snapshot: dict[str, Any]) -> dict[str, Any]:
    """What the presented performance figures mean.

    `fee_drag` carries the signed gross-minus-net difference for the presented
    period, computed here from the RAW captured returns because Render must
    not derive it from two displayed numbers - those are rounded, and a
    difference of roundings is a different number than a rounding of the
    difference. The field is named for what it IS (gross minus net, in
    percentage points) rather than what it approximates: compounding means the
    difference is not exactly "fees", which is why the agreed page wording
    says "approximately".

    Decided on #247: one line under the period table, never a gross column.
    Absent gross means no figure - never a guessed drag - while a genuine
    zero is kept, because "fees cost you nothing this period" is a finding
    and "we cannot say" is not. The sign is preserved: a negative difference
    (net above gross - rebates) must not be silently hidden or clamped, and
    the page sentence follows the sign rather than assuming it.
    """

    summary = _as_dict(_as_dict(snapshot.get("performance")).get("summary"))
    period_summary = _as_dict(summary.get(_FEE_DRAG_PERIOD))
    gross = _optional_decimal(period_summary.get("gross_cumulative_return"))
    net = _optional_decimal(period_summary.get(_PRESENTED_CUMULATIVE_RETURN_KEY))
    fee_drag = None
    if gross is not None and net is not None:
        fee_drag = {
            "period": _FEE_DRAG_PERIOD,
            "gross_minus_net_pp": f"{(gross - net).quantize(Decimal('0.01'))}",
        }
    return {"return_basis": PRESENTED_RETURN_BASIS, "fee_drag": fee_drag}


def _performance_periods(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    summary = _as_dict(_as_dict(snapshot.get("performance")).get("summary"))
    periods: list[dict[str, str]] = []
    for period_code in ("1M", "3M", "YTD", "1Y", "5Y", "SI"):
        period_summary = _as_dict(summary.get(period_code))
        if not period_summary:
            continue
        periods.append(
            {
                "period": period_code,
                "net_return_pct": _percent_text(
                    period_summary.get(_PRESENTED_CUMULATIVE_RETURN_KEY)
                ),
                "benchmark_return_pct": _percent_text(
                    period_summary.get("benchmark_cumulative_return")
                ),
                "relative_return_pct": _percent_text(
                    period_summary.get("benchmark_relative_return")
                ),
            }
        )
    return periods


def _performance_summary_table(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    summary = _as_dict(_as_dict(snapshot.get("performance")).get("summary"))
    rows: list[dict[str, str]] = []
    for label, period_code in (
        ("Current month", "1M"),
        ("Current quarter", "3M"),
        ("Year-to-date", "YTD"),
        ("Last 12 months", "1Y"),
        ("Since inception", "SI"),
    ):
        period_summary = _as_dict(summary.get(period_code))
        if not period_summary:
            continue
        rows.append(
            {
                "label": label,
                "period": period_code,
                "net_return_pct": _percent_text(
                    period_summary.get(_PRESENTED_CUMULATIVE_RETURN_KEY)
                ),
                "annualized_return_pct": _percent_text(
                    period_summary.get(_PRESENTED_ANNUALIZED_RETURN_KEY)
                ),
            }
        )
    return rows


def _performance_history(
    snapshot: dict[str, Any],
    key: str,
    *,
    limit: int,
) -> list[dict[str, str]]:
    rows = _as_dict(snapshot.get("performance")).get(key)
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in rows[-limit:]:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "period": _optional_str(item.get("period")) or "Not available",
                "period_start": _optional_str(item.get("period_start")) or "Not available",
                "period_end": _optional_str(item.get("period_end")) or "Not available",
                "final_value": _decimal_text(item.get("end_market_value")),
                "inflows": _decimal_text(item.get("inflows")),
                "outflows": _decimal_text(item.get("outflows")),
                "performance_value": _decimal_text(item.get("performance_value")),
                "cumulative_performance_value": _decimal_text(
                    item.get("cumulative_performance_value")
                ),
                "twr_pct": _percent_text(item.get("twr_pct")),
                "cumulative_twr_pct": _percent_text(item.get("cumulative_twr_pct")),
            }
        )
    return normalized


def _benchmark_series_section(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The benchmark's monthly series for the cumulative chart (report#288).

    Postures per the locked contract: ``ready`` (the source stated the
    series - points forwarded verbatim at the portfolio rows' precision
    treatment, capped to the same 12-month window, missing months simply
    missing), ``unbenchmarked`` (no benchmark assigned or requested - a
    normal state, no caption), ``unavailable`` (a benchmark was expected
    but the source stated no series - carries the source's own diagnostics
    sentence where present, else the stated supportability note). A stated
    posture is never an empty line: an available-but-empty series reads
    unavailable, not ready.
    """

    performance = _as_dict(snapshot.get("performance"))
    context = _as_dict(performance.get("benchmark"))
    rows = performance.get("benchmark_monthly_history")
    points: list[dict[str, str]] = []
    for item in (rows if isinstance(rows, list) else [])[-12:]:
        if not isinstance(item, dict):
            continue
        points.append(
            {
                "period": _optional_str(item.get("period")) or "Not available",
                "period_start": _optional_str(item.get("period_start")) or "Not available",
                "period_end": _optional_str(item.get("period_end")) or "Not available",
                "twr_pct": _percent_text(item.get("twr_pct")),
                "cumulative_twr_pct": _percent_text(item.get("cumulative_twr_pct")),
            }
        )
    status = _optional_str(context.get("comparison_status"))
    if status == "available" and points:
        return {
            "posture": "ready",
            "benchmark_id": _optional_str(context.get("benchmark_code")) or "Not available",
            "benchmark_currency": _optional_str(context.get("benchmark_currency")),
            "return_source": _optional_str(context.get("return_source")) or "Not available",
            "points": points,
        }
    if status in {"not_requested", None} and not points:
        return {"posture": "unbenchmarked", "points": []}
    statement = _optional_str(context.get("source_statement"))
    if statement is None:
        statement = _supportability_note_message(
            performance, code="benchmark_comparison_unavailable"
        )
    return {
        "posture": "unavailable",
        "benchmark_id": _optional_str(context.get("benchmark_code")),
        "points": [],
        "source_statement": statement
        or "Benchmark return series is not sourced in this report response.",
    }


def _supportability_note_message(performance: dict[str, Any], *, code: str) -> str | None:
    notes = _as_dict(performance.get("supportability")).get("notes")
    for note in notes if isinstance(notes, list) else []:
        if isinstance(note, dict) and note.get("code") == code:
            return _optional_str(note.get("message"))
    return None


#: How many holdings the overview panel presents. The reconciliation published
#: beside it describes exactly this set, so the two are defined together.
PRESENTED_HOLDING_LIMIT = 5


def _ranked_holdings(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Every holding, largest first, with the raw values ranking needs.

    Split out so the panel and the reconciliation that explains it are drawn
    from ONE ordered list. Two sorts would be two chances to describe different
    sets, and a reconciliation describing a set that is not on the page is
    worse than none - it looks checked.
    """

    grouped_holdings = _as_dict(snapshot.get("holdings")).get("holdingsByAssetClass")
    if not isinstance(grouped_holdings, dict):
        return []
    flattened: list[dict[str, Any]] = []
    for asset_class, items in grouped_holdings.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            flattened.append(
                {
                    "asset_class": str(asset_class),
                    "security_name": _holding_name(item) or "Unknown holding",
                    "weight_pct": _percent_text(item.get("weight")),
                    "quantity": _decimal_text(item.get("quantity")),
                    "currency": _optional_str(item.get("currency")) or "Not available",
                    "security_id": _optional_str(item.get("security_id")) or "Not available",
                    "instrument_name": _optional_str(item.get("instrument_name"))
                    or "Not available",
                    "isin": _optional_str(item.get("isin")) or "Not available",
                    "position_date": _optional_str(item.get("position_date")) or "Not available",
                    "product_type": _optional_str(item.get("product_type")) or "Not available",
                    "sector": _optional_str(item.get("sector")) or "Not available",
                    "country_of_risk": _optional_str(item.get("country_of_risk"))
                    or "Not available",
                    "rating": _optional_str(item.get("rating")) or "Not available",
                    "liquidity_tier": _optional_str(item.get("liquidity_tier")) or "Not available",
                    "held_since_date": _optional_str(item.get("held_since_date"))
                    or "Not available",
                    "market_price": _decimal_text(item.get("market_price")),
                    "cost_basis_reporting_currency": _decimal_text(
                        item.get("cost_basis_reporting_currency")
                    ),
                    "cost_basis_local": _decimal_text(item.get("cost_basis_local")),
                    "market_value": _decimal_text(item.get("market_value_reporting_currency")),
                    "market_value_local": _decimal_text(item.get("market_value_local")),
                    "unrealized_pnl": _decimal_text(item.get("unrealized_pnl_reporting_currency")),
                    "unrealized_pnl_local": _decimal_text(item.get("unrealized_pnl_local")),
                    "unrealized_pnl_pct": _percent_text(item.get("unrealized_pnl_pct")),
                    "ytd_contribution_pct": _percent_text(item.get("ytd_contribution_pct")),
                    "ytd_average_weight_pct": _percent_text(item.get("ytd_average_weight_pct")),
                    "ytd_total_return_pct": _percent_text(item.get("ytd_total_return_pct")),
                    "_sort_value": _optional_decimal(item.get("market_value_reporting_currency"))
                    or Decimal("0"),
                    # Raw, for the reconciliation. `weight_pct` above is
                    # already formatted text and summing that back would be
                    # reading our own output instead of the evidence.
                    "_weight": _optional_decimal(item.get("weight")),
                }
            )
    flattened.sort(key=lambda item: item["_sort_value"], reverse=True)
    return flattened


def _positions(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """The published position rows, in presentation order.

    The field list is explicit rather than a strip of underscore-prefixed keys:
    this is the package contract, and an internal field must not reach Render
    just because someone added it to the ranking row above.
    """

    positions: list[dict[str, str]] = []
    for item in _ranked_holdings(snapshot):
        positions.append(
            {
                "asset_class": item["asset_class"],
                "security_name": item["security_name"],
                "weight_pct": item["weight_pct"],
                "quantity": item["quantity"],
                "currency": item["currency"],
                "security_id": item["security_id"],
                "instrument_name": item["instrument_name"],
                "isin": item["isin"],
                "position_date": item["position_date"],
                "product_type": item["product_type"],
                "sector": item["sector"],
                "country_of_risk": item["country_of_risk"],
                "rating": item["rating"],
                "liquidity_tier": item["liquidity_tier"],
                "held_since_date": item["held_since_date"],
                "market_price": item["market_price"],
                "cost_basis_reporting_currency": item["cost_basis_reporting_currency"],
                "cost_basis_local": item["cost_basis_local"],
                "market_value": item["market_value"],
                "market_value_local": item["market_value_local"],
                "unrealized_pnl": item["unrealized_pnl"],
                "unrealized_pnl_local": item["unrealized_pnl_local"],
                "unrealized_pnl_pct": item["unrealized_pnl_pct"],
                "ytd_contribution_pct": item["ytd_contribution_pct"],
                "ytd_average_weight_pct": item["ytd_average_weight_pct"],
                "ytd_total_return_pct": item["ytd_total_return_pct"],
            }
        )
    return positions


def _top_holdings(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    return _positions(snapshot)[:PRESENTED_HOLDING_LIMIT]


def _transactions(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    transactions = _as_dict(snapshot.get("transactions"))
    grouped_transactions = _as_dict(transactions.get("transactionsByCategory"))
    if not grouped_transactions:
        grouped_transactions = _as_dict(transactions.get("transactionsByAssetClass"))
    if not grouped_transactions:
        return []

    flattened: list[dict[str, Any]] = []
    for category, items in grouped_transactions.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            flattened.append(
                {
                    "category": _optional_str(category) or "Other",
                    "asset_class": _optional_str(item.get("asset_class")) or "Not available",
                    "transaction_category": _optional_str(item.get("transaction_category"))
                    or "Not available",
                    "display_label": _optional_str(item.get("display_label"))
                    or _optional_str(item.get("transaction_type"))
                    or "Transaction",
                    "cash_leg": "Yes" if bool(item.get("cash_leg")) else "No",
                    "transaction_id": _optional_str(item.get("transaction_id")) or "Not available",
                    "trade_date": _optional_str(item.get("transaction_date")) or "Not available",
                    "transaction_type": _optional_str(item.get("transaction_type"))
                    or "Not available",
                    "instrument_id": _optional_str(item.get("instrument_id")) or "Not available",
                    "security_id": _optional_str(item.get("security_id")) or "Not available",
                    "amount": _decimal_text(item.get("amount_reporting_currency")),
                    "gross_amount_reporting_currency": _decimal_text(
                        item.get("gross_transaction_amount_reporting_currency")
                    ),
                    "realized_pnl_reporting_currency": _decimal_text(
                        item.get("realized_pnl_reporting_currency")
                    ),
                    "realized_pnl_local": _decimal_text(item.get("realized_pnl_local")),
                    "net_interest_amount_reporting_currency": _decimal_text(
                        item.get("net_interest_amount_reporting_currency")
                    ),
                    "withholding_tax_amount_reporting_currency": _decimal_text(
                        item.get("withholding_tax_amount_reporting_currency")
                    ),
                    "income_or_tax_reporting_currency": _decimal_text(
                        item.get("income_or_tax_reporting_currency")
                    ),
                    "_sort_date": _optional_str(item.get("transaction_date")) or "",
                }
            )
    flattened.sort(key=lambda item: (item["_sort_date"], item["transaction_id"]))
    return [
        {
            "category": item["category"],
            "asset_class": item["asset_class"],
            "transaction_category": item["transaction_category"],
            "display_label": item["display_label"],
            "cash_leg": item["cash_leg"],
            "transaction_id": item["transaction_id"],
            "trade_date": item["trade_date"],
            "transaction_type": item["transaction_type"],
            "instrument_id": item["instrument_id"],
            "security_id": item["security_id"],
            "amount": item["amount"],
            "gross_amount_reporting_currency": item["gross_amount_reporting_currency"],
            "realized_pnl_reporting_currency": item["realized_pnl_reporting_currency"],
            "realized_pnl_local": item["realized_pnl_local"],
            "net_interest_amount_reporting_currency": item[
                "net_interest_amount_reporting_currency"
            ],
            "withholding_tax_amount_reporting_currency": item[
                "withholding_tax_amount_reporting_currency"
            ],
            "income_or_tax_reporting_currency": item["income_or_tax_reporting_currency"],
        }
        for item in flattened
    ]


def _allocation_presentation(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """The document's dimension decision, recorded at capture time.

    A snapshot captured before this key existed is resolved with the same
    function, so a rerender of an older job presents what that order asked for
    rather than what a renderer would have guessed (issue #224).
    """

    recorded = snapshot.get("allocation_presentation")
    if isinstance(recorded, dict) and isinstance(recorded.get("dimensions"), list):
        return recorded
    return resolve_allocation_presentation(options=job.options, snapshot=snapshot)


def _allocation_breakdowns(
    snapshot: dict[str, Any],
) -> dict[str, list[dict[str, str | int | None]]]:
    allocation = _as_dict(snapshot.get("allocation"))
    return {
        "by_asset_class": _allocation_bucket_rows(allocation.get("byAssetClass")),
        "by_currency": _allocation_bucket_rows(allocation.get("byCurrency")),
        "by_region": _allocation_bucket_rows(allocation.get("byRegion")),
        "by_sector": _allocation_bucket_rows(allocation.get("bySector")),
        "by_country": _allocation_bucket_rows(allocation.get("byCountry")),
        "by_product_type": _allocation_bucket_rows(allocation.get("byProductType")),
        "by_rating": _allocation_bucket_rows(allocation.get("byRating")),
    }


def _allocation_bucket_rows(buckets: object) -> list[dict[str, str | int | None]]:
    if not isinstance(buckets, list):
        return []
    rows: list[tuple[Decimal, dict[str, str | int | None]]] = []
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        row = {
            "name": _optional_str(bucket.get("group")) or "Not available",
            "weight_pct": _percent_text(bucket.get("weight")),
            "market_value": _decimal_text(bucket.get("market_value")),
            "position_count": _optional_int(bucket.get("position_count")),
        }
        rows.append(
            (
                _optional_decimal(bucket.get("market_value")) or Decimal("0"),
                row,
            )
        )
    rows.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in rows]


def _holding_name(item: dict[str, Any]) -> str | None:
    return (
        _optional_str(item.get("security_name"))
        or _optional_str(item.get("instrument_name"))
        or _optional_str(item.get("security_id"))
    )


def _percent_text(value: object) -> str:
    decimal_value = _optional_decimal(value)
    if decimal_value is None:
        return "Not available"
    return f"{decimal_value.quantize(Decimal('0.01'))}%"


def _decimal_text(value: object) -> str:
    decimal_value = _optional_decimal(value)
    if decimal_value is None:
        return "Not available"
    return str(decimal_value.quantize(Decimal("0.01")))


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _reviewed_advisory_narrative(snapshot: dict[str, Any]) -> dict[str, Any]:
    package = _as_dict(snapshot.get("proposal_narrative_package"))
    if not package:
        return {"status": "not_supplied", "sections": [], "disclosures": []}
    review = _as_dict(package.get("review"))
    source_lineage = _as_dict(package.get("source_lineage"))
    sections = [
        {
            "section_id": _optional_str(section.get("section_id")) or "not_available",
            "title": _optional_str(section.get("title")) or "Not available",
            "body": _optional_str(section.get("body")) or "",
            "source_refs": [
                item for item in section.get("source_refs", []) if isinstance(item, dict)
            ],
        }
        for section in package.get("sections", [])
        if isinstance(section, dict)
    ]
    disclosures = [
        {
            "disclosure_id": _optional_str(disclosure.get("disclosure_id")) or "not_available",
            "text": _optional_str(disclosure.get("text")),
        }
        for disclosure in package.get("disclosures", [])
        if isinstance(disclosure, dict)
    ]
    return {
        "status": "included",
        "package_status": _optional_str(package.get("package_status")) or "not_available",
        "usage": _optional_str(package.get("usage")) or "not_available",
        "proposal_id": _optional_str(package.get("proposal_id")) or "not_available",
        "proposal_version_no": _optional_int(package.get("proposal_version_no")),
        "narrative_id": _optional_str(package.get("narrative_id")) or "not_available",
        "narrative_status": _optional_str(package.get("narrative_status")) or "not_available",
        "audience": _optional_str(package.get("audience")) or "not_available",
        "policy_version": _optional_str(package.get("policy_version")) or "not_available",
        "review": {
            "review_id": _optional_str(review.get("review_id")) or "not_available",
            "review_state": _optional_str(review.get("review_state")) or "not_available",
            "reviewed_at": _optional_str(review.get("reviewed_at")),
            "reviewed_by": _optional_str(review.get("reviewed_by")) or "not_available",
        },
        "source_lineage": {
            "source_narrative_hash": _optional_str(source_lineage.get("source_narrative_hash"))
            or "not_available",
            "proposal_hash": _optional_str(source_lineage.get("proposal_hash")),
            "proposal_version_hash": _optional_str(source_lineage.get("proposal_version_hash")),
        },
        "sections": sections,
        "disclosures": disclosures,
        "guardrail_results": [
            item for item in package.get("guardrail_results", []) if isinstance(item, dict)
        ],
        "limitations": [item for item in package.get("limitations", []) if isinstance(item, dict)],
        "execution_boundary": _as_dict(package.get("execution_boundary")),
        "ai_lineage": _as_dict(package.get("ai_lineage")),
    }


def _advisor_commentary(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The accepted Advisor Brief commentary section, resolved and bounded at
    capture time by the report's own advisor-commentary resolution (issue
    #166). The package is report-authored, so it passes through verbatim; a
    snapshot without one renders the not_supplied posture."""

    package = _as_dict(snapshot.get("advisor_commentary_package"))
    if not package:
        return {"status": "not_supplied"}
    return package


def _advisor_proposal_memo(snapshot: dict[str, Any]) -> dict[str, Any]:
    package = _as_dict(snapshot.get("proposal_memo_package"))
    if not package:
        return {"status": "not_supplied", "sections": [], "disclosures": []}
    review = _as_dict(package.get("review"))
    sections = []
    disclosures = []
    for section in package.get("sections", []):
        if not isinstance(section, dict):
            continue
        material_claims = [
            claim for claim in section.get("material_claims", []) if isinstance(claim, dict)
        ]
        sections.append(
            {
                "section_id": _optional_str(section.get("section_id")) or "not_available",
                "title": _optional_str(section.get("title")) or "Not available",
                "status": _optional_str(section.get("status")) or "not_available",
                "summary": _optional_str(section.get("summary")) or "",
                "material_claims": material_claims,
                "evidence_refs": _string_list(section.get("evidence_refs")),
                "reason_codes": _string_list(section.get("reason_codes")),
            }
        )
        if section.get("section_id") == "CONFLICTS_AND_DISCLOSURES":
            for claim in material_claims:
                claim_id = _optional_str(claim.get("claim_id"))
                text = _optional_str(claim.get("text"))
                if claim_id:
                    disclosures.append({"disclosure_id": claim_id, "text": text})
    return {
        "status": "included",
        "package_status": _optional_str(package.get("package_status")) or "not_available",
        "usage": _optional_str(package.get("usage")) or "not_available",
        "memo_id": _optional_str(package.get("memo_id")) or "not_available",
        "memo_version": _optional_str(package.get("memo_version")) or "not_available",
        "memo_status": _optional_str(package.get("memo_status")) or "not_available",
        "proposal_id": _optional_str(package.get("proposal_id")) or "not_available",
        "proposal_version_no": _optional_int(package.get("proposal_version_no")),
        "memo_hash": _optional_str(package.get("memo_hash")) or "not_available",
        "source_input_hash": _optional_str(package.get("source_input_hash")) or "not_available",
        "client_ready_publication": _optional_str(package.get("client_ready_publication"))
        or "BLOCKED",
        "review": {
            "review_event_id": _optional_str(review.get("review_event_id")) or "not_available",
            "review_action": _optional_str(review.get("review_action")) or "not_available",
            "reviewed_by": _optional_str(review.get("reviewed_by")) or "not_available",
            "reviewed_at": _optional_str(review.get("reviewed_at")),
        },
        "sections": sections,
        "disclosures": disclosures,
        "source_authority_manifest": _as_dict(package.get("source_authority_manifest")),
        "supportability": _as_dict(package.get("supportability")),
    }


def _reviewed_narrative_lineage_refs(reviewed_narrative: dict[str, Any]) -> list[str]:
    source_lineage = _as_dict(reviewed_narrative.get("source_lineage"))
    refs = [
        f"lotus-advise:proposal:{reviewed_narrative.get('proposal_id')}",
        f"lotus-advise:proposal-narrative:{reviewed_narrative.get('narrative_id')}",
        _optional_str(source_lineage.get("source_narrative_hash")),
    ]
    review = _as_dict(reviewed_narrative.get("review"))
    review_id = _optional_str(review.get("review_id"))
    if review_id and review_id != "not_available":
        refs.append(f"lotus-advise:proposal-narrative-review:{review_id}")
    return [ref for ref in refs if ref and "not_available" not in ref]


def _advisor_memo_lineage_refs(advisor_memo: dict[str, Any]) -> list[str]:
    refs = [
        f"lotus-advise:proposal:{advisor_memo.get('proposal_id')}",
        f"lotus-advise:proposal-memo:{advisor_memo.get('memo_id')}",
        _optional_str(advisor_memo.get("memo_hash")),
        _optional_str(advisor_memo.get("source_input_hash")),
    ]
    review = _as_dict(advisor_memo.get("review"))
    review_id = _optional_str(review.get("review_event_id"))
    if review_id and review_id != "not_available":
        refs.append(f"lotus-advise:proposal-memo-review:{review_id}")
    return [ref for ref in refs if ref and "not_available" not in ref]


def _reviewed_narrative_disclosure_refs(reviewed_narrative: dict[str, Any]) -> list[str]:
    disclosures = reviewed_narrative.get("disclosures")
    if not isinstance(disclosures, list):
        return []
    return [
        disclosure_id
        for disclosure in disclosures
        if isinstance(disclosure, dict)
        for disclosure_id in [_optional_str(disclosure.get("disclosure_id"))]
        if disclosure_id and disclosure_id != "not_available"
    ]


def _advisor_memo_disclosure_refs(advisor_memo: dict[str, Any]) -> list[str]:
    disclosures = advisor_memo.get("disclosures")
    if not isinstance(disclosures, list):
        return []
    return [
        disclosure_id
        for disclosure in disclosures
        if isinstance(disclosure, dict)
        for disclosure_id in [_optional_str(disclosure.get("disclosure_id"))]
        if disclosure_id and disclosure_id != "not_available"
    ]


def _dedupe_strings(values: Sequence[str | None]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _optional_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _optional_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Real):
        return Decimal(str(value))
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None
