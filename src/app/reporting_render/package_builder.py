from __future__ import annotations

from decimal import Decimal, InvalidOperation
from numbers import Real
from typing import Any

from app.reporting_jobs.models import ReportJobLedgerRecord


def _build_render_package(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
) -> dict[str, Any]:
    if job.report_type == "proof_pack":
        return _build_proof_pack_render_package(
            job=job,
            snapshot=snapshot,
            render_job_id=render_job_id,
        )
    if job.report_type == "outcome_review":
        return _build_outcome_review_render_package(
            job=job,
            snapshot=snapshot,
            render_job_id=render_job_id,
        )
    if job.report_type == "rebalance_wave":
        return _build_wave_render_package(
            job=job,
            snapshot=snapshot,
            render_job_id=render_job_id,
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
        "mandate": {
            "objective": _optional_str(_as_dict(key_figures.get("client_profile")).get("objective"))
            or _optional_str(_as_dict(snapshot.get("methodology")).get("investment_objective"))
            or "Objective not available in the governed snapshot.",
            "risk_exposure": _optional_str(mandate_profile.get("risk_exposure")) or "not_available",
            "booking_center_code": _optional_str(identity.get("booking_center_code"))
            or "not_available",
            "advisor_id": _optional_str(identity.get("advisor_id")) or "not_available",
        },
        "portfolio_metrics": {
            "invested_value": _decimal_text(
                portfolio_value.get("invested_market_value_reporting_currency")
            ),
            "cash_balance": _decimal_text(portfolio_value.get("cash_balance_reporting_currency")),
            "cash_weight_pct": _percent_text(portfolio_value.get("cash_weight_pct")),
        },
        "allocation_summary": {
            "largest_asset_class_name": _optional_str(allocation.get("name")) or "Not available",
            "largest_asset_class_weight_pct": _percent_text(allocation.get("weight_pct")),
            "largest_asset_class_market_value": _decimal_text(
                allocation.get("market_value_reporting_currency")
            ),
            "largest_asset_class_position_count": _optional_int(allocation.get("position_count")),
        },
        "allocation_breakdowns": _allocation_breakdowns(snapshot),
        "performance_periods": _performance_periods(snapshot),
        "performance_summary_table": _performance_summary_table(snapshot),
        "performance_monthly_history": _performance_history(
            snapshot,
            "monthly_history",
            limit=12,
        ),
        "performance_annual_history": _performance_history(
            snapshot,
            "annual_history",
            limit=8,
        ),
        "performance_highlight": {
            "largest_positive_contributor_name": _holding_name(
                _as_dict(performance.get("largest_positive_contributor"))
            )
            or "Not available",
            "largest_positive_contribution_pct": _percent_text(
                _as_dict(performance.get("largest_positive_contributor")).get(
                    "total_contribution_pct"
                )
                or _as_dict(performance.get("largest_positive_contributor")).get(
                    "ytd_contribution_pct"
                )
            ),
            "benchmark_comparison_status": _optional_str(
                performance.get("benchmark_comparison_status")
            )
            or "not_available",
        },
        "transaction_period_label": (
            f"From {job.as_of_date.replace(month=1, day=1).strftime('%d.%m.%Y')} "
            f"to {job.as_of_date.strftime('%d.%m.%Y')}"
        ),
        "risk_summary": {
            "volatility_pct": _percent_text(
                _as_dict(_as_dict(snapshot.get("riskAnalytics")).get("summary"))
                .get("YTD", {})
                .get("volatility")
                or risk.get("ytd_volatility_pct")
            ),
            "beta": _decimal_text(
                _as_dict(_as_dict(snapshot.get("riskAnalytics")).get("summary"))
                .get("YTD", {})
                .get("beta")
                or risk.get("ytd_beta")
            ),
            "tracking_error_pct": _percent_text(
                _as_dict(_as_dict(snapshot.get("riskAnalytics")).get("summary"))
                .get("YTD", {})
                .get("tracking_error")
                or risk.get("ytd_tracking_error_pct")
            ),
            "information_ratio": _decimal_text(
                _as_dict(_as_dict(snapshot.get("riskAnalytics")).get("summary"))
                .get("YTD", {})
                .get("information_ratio")
                or risk.get("ytd_information_ratio")
            ),
            "value_at_risk_pct": _percent_text(
                _as_dict(_as_dict(snapshot.get("riskAnalytics")).get("summary"))
                .get("YTD", {})
                .get("value_at_risk")
            ),
        },
        "top_holdings": _top_holdings(snapshot),
        "positions": _positions(snapshot),
        "transactions": _transactions(snapshot),
        "governance_summary": {
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
        },
    }
    return {
        "render_package_version": "render_package.v1",
        "render_job_id": render_job_id,
        "report_job_id": job.job_id,
        "snapshot_id": snapshot.get("snapshot_id") or f"snapshot-for-{job.job_id}",
        "report_type": job.report_type,
        "report_data_contract_version": "portfolio_review.v1",
        "template_id": "portfolio-review",
        "template_version": "v1",
        "locale": "en-SG",
        "brand_variant": "private_banking",
        "output_format": "pdf",
        "render_context": {"timezone": "Asia/Singapore"},
        "report_data": report_data,
        "lineage_refs": [job.job_id],
        "disclosure_refs": ["portfolio-review.standard-disclosures.v1"],
        "requested_by": job.triggered_by,
        "correlation_id": job.correlation_id,
        "trace_id": job.trace_id,
    }


def _build_proof_pack_render_package(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
) -> dict[str, Any]:
    sections = [
        {
            "section_id": _optional_str(item.get("section_id")) or "not_available",
            "section_type": _optional_str(item.get("section_type")) or "not_available",
            "state": _optional_str(item.get("state")) or "not_available",
            "title": _optional_str(item.get("title")) or "Not available",
            "summary": _optional_str(item.get("summary")) or "No section summary supplied.",
            "reason_codes": _string_list(item.get("reason_codes")),
            "content_hash": _optional_str(item.get("content_hash")) or "not_available",
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
        "content_hash": _optional_str(snapshot.get("content_hash")) or "not_available",
        "proof_pack_content_hash": _optional_str(snapshot.get("proof_pack_content_hash"))
        or "not_available",
        "redaction_policy": _optional_str(snapshot.get("redaction_policy")) or "NO_RAW_PAYLOADS",
        "portfolio_memory": portfolio_memory,
    }
    return {
        "render_package_version": "render_package.v1",
        "render_job_id": render_job_id,
        "report_job_id": job.job_id,
        "snapshot_id": snapshot.get("snapshot_id") or f"snapshot-for-{job.job_id}",
        "report_type": job.report_type,
        "report_data_contract_version": "dpm_proof_pack_report_input.v1",
        "template_id": "proof-pack",
        "template_version": "v1",
        "locale": "en-SG",
        "brand_variant": "private_banking",
        "output_format": "pdf",
        "render_context": {"timezone": "Asia/Singapore"},
        "report_data": report_data,
        "lineage_refs": _dpm_lineage_refs(
            job.job_id,
            report_data["proof_pack_id"],
            report_data["content_hash"],
            portfolio_memory,
        ),
        "disclosure_refs": ["proof-pack.standard-disclosures.v1"],
        "requested_by": job.triggered_by,
        "correlation_id": job.correlation_id,
        "trace_id": job.trace_id,
    }


def _build_outcome_review_render_package(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
) -> dict[str, Any]:
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
        "content_hash": _optional_str(snapshot.get("content_hash")) or "not_available",
        "outcome_review_content_hash": _optional_str(snapshot.get("outcome_review_content_hash"))
        or "not_available",
        "redaction_policy": _optional_str(snapshot.get("redaction_policy")) or "NO_RAW_PAYLOADS",
        "portfolio_memory": portfolio_memory,
    }
    return {
        "render_package_version": "render_package.v1",
        "render_job_id": render_job_id,
        "report_job_id": job.job_id,
        "snapshot_id": snapshot.get("snapshot_id") or f"snapshot-for-{job.job_id}",
        "report_type": job.report_type,
        "report_data_contract_version": "dpm_outcome_report_input.v1",
        "template_id": "outcome-review",
        "template_version": "v1",
        "locale": "en-SG",
        "brand_variant": "private_banking",
        "output_format": "pdf",
        "render_context": {"timezone": "Asia/Singapore"},
        "report_data": report_data,
        "lineage_refs": _dpm_lineage_refs(
            job.job_id,
            report_data["outcome_review_id"],
            report_data["content_hash"],
            portfolio_memory,
        ),
        "disclosure_refs": ["outcome-review.standard-disclosures.v1"],
        "requested_by": job.triggered_by,
        "correlation_id": job.correlation_id,
        "trace_id": job.trace_id,
    }


def _build_wave_render_package(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
) -> dict[str, Any]:
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
        "content_hash": _optional_str(snapshot.get("content_hash")) or "not_available",
        "wave_content_hash": _optional_str(snapshot.get("wave_content_hash")) or "not_available",
        "redaction_policy": _optional_str(snapshot.get("redaction_policy")) or "NO_RAW_PAYLOADS",
        "portfolio_memory": portfolio_memory,
    }
    return {
        "render_package_version": "render_package.v1",
        "render_job_id": render_job_id,
        "report_job_id": job.job_id,
        "snapshot_id": snapshot.get("snapshot_id") or f"snapshot-for-{job.job_id}",
        "report_type": job.report_type,
        "report_data_contract_version": "dpm_wave_report_input.v1",
        "template_id": "rebalance-wave",
        "template_version": "v1",
        "locale": "en-SG",
        "brand_variant": "private_banking",
        "output_format": "pdf",
        "render_context": {"timezone": "Asia/Singapore"},
        "report_data": report_data,
        "lineage_refs": _dpm_lineage_refs(
            job.job_id,
            report_data["wave_id"],
            report_data["content_hash"],
            portfolio_memory,
        ),
        "disclosure_refs": ["rebalance-wave.standard-disclosures.v1"],
        "requested_by": job.triggered_by,
        "correlation_id": job.correlation_id,
        "trace_id": job.trace_id,
    }


def _portfolio_memory_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    context = _as_dict(snapshot.get("portfolio_memory_context"))
    if not context:
        return {
            "status": "not_supplied",
            "event_count": 0,
            "content_hash": "not_available",
            "event_refs": [],
            "governance_policy": {},
        }

    raw_event_refs = context.get("event_refs")
    if not isinstance(raw_event_refs, list):
        raw_event_refs = []
    event_refs = [
        {
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
        }
        for item in raw_event_refs[:12]
        if isinstance(item, dict)
    ]
    return {
        "status": "supplied",
        "portfolio_id": _optional_str(context.get("portfolio_id")) or "not_available",
        "supportability_state": _optional_str(context.get("supportability_state"))
        or "not_available",
        "event_count": _optional_int(context.get("event_count")) or len(event_refs),
        "source_systems": _string_list(context.get("source_systems")),
        "reason_codes": _string_list(context.get("reason_codes")),
        "content_hash": _optional_str(context.get("content_hash")) or "not_available",
        "governance_policy": _as_dict(context.get("governance_policy")),
        "event_refs": event_refs,
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
                "net_return_pct": _percent_text(period_summary.get("net_cumulative_return")),
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
                "net_return_pct": _percent_text(period_summary.get("net_cumulative_return")),
                "annualized_return_pct": _percent_text(period_summary.get("net_annualized_return")),
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


def _positions(snapshot: dict[str, Any]) -> list[dict[str, str]]:
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
                }
            )
    flattened.sort(key=lambda item: item["_sort_value"], reverse=True)
    positions: list[dict[str, str]] = []
    for item in flattened:
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
    return _positions(snapshot)[:5]


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
