from __future__ import annotations

from decimal import Decimal, InvalidOperation
from numbers import Real
from typing import Any, Sequence

from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_lineage.allocation_presentation import resolve_allocation_presentation
from app.reporting_render.contribution_ranking import build_contribution_ranking


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
        "performance_highlight": _performance_highlight_section(performance),
        "contribution_ranking": build_contribution_ranking(snapshot),
        "transaction_period_label": (
            f"From {job.as_of_date.replace(month=1, day=1).strftime('%d.%m.%Y')} "
            f"to {job.as_of_date.strftime('%d.%m.%Y')}"
        ),
        "risk_summary": _risk_summary_section(snapshot, risk),
        "top_holdings": _top_holdings(snapshot),
        "positions": _positions(snapshot),
        "transactions": _transactions(snapshot),
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
    disclosure_refs = ["portfolio-review.standard-disclosures.v1"]
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
        report_data_contract_version="portfolio_review.v1",
        template_id="portfolio-review",
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


def _risk_summary_section(snapshot: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
    return {
        "volatility_pct": _percent_text(
            _ytd_risk_metric(snapshot, "volatility") or risk.get("ytd_volatility_pct")
        ),
        "beta": _decimal_text(_ytd_risk_metric(snapshot, "beta") or risk.get("ytd_beta")),
        "tracking_error_pct": _percent_text(
            _ytd_risk_metric(snapshot, "tracking_error") or risk.get("ytd_tracking_error_pct")
        ),
        "information_ratio": _decimal_text(
            _ytd_risk_metric(snapshot, "information_ratio") or risk.get("ytd_information_ratio")
        ),
        "value_at_risk_pct": _percent_text(_ytd_risk_metric(snapshot, "value_at_risk")),
    }


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


def _render_package_envelope(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
    report_data_contract_version: str,
    template_id: str,
    report_data: dict[str, Any],
    lineage_refs: list[str],
    disclosure_refs: list[str],
) -> dict[str, Any]:
    """The envelope every render package shares; only the typed content varies.

    One definition instead of four verbatim copies, so an envelope change (a new
    locale policy, a context field) cannot land in three report families and miss
    the fourth.
    """

    return {
        "render_package_version": "render_package.v1",
        "render_job_id": render_job_id,
        "report_job_id": job.job_id,
        "snapshot_id": snapshot.get("snapshot_id") or f"snapshot-for-{job.job_id}",
        "report_type": job.report_type,
        "report_data_contract_version": report_data_contract_version,
        "template_id": template_id,
        "template_version": "v1",
        "locale": "en-SG",
        "brand_variant": "private_banking",
        "output_format": "pdf",
        "render_context": {"timezone": "Asia/Singapore"},
        "report_data": report_data,
        "lineage_refs": lineage_refs,
        "disclosure_refs": disclosure_refs,
        "requested_by": job.triggered_by,
        "correlation_id": job.correlation_id,
        "trace_id": job.trace_id,
    }


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
        report_data_contract_version="dpm_proof_pack_report_input.v1",
        template_id="proof-pack",
        report_data=report_data,
        lineage_refs=_dpm_lineage_refs(
            job.job_id,
            report_data["proof_pack_id"],
            report_data["content_hash"],
            portfolio_memory,
        ),
        disclosure_refs=["proof-pack.standard-disclosures.v1"],
    )


def _build_outcome_review_render_package(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
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
        report_data_contract_version="dpm_outcome_report_input.v1",
        template_id="outcome-review",
        report_data=report_data,
        lineage_refs=_dpm_lineage_refs(
            job.job_id,
            report_data["outcome_review_id"],
            report_data["content_hash"],
            portfolio_memory,
        ),
        disclosure_refs=["outcome-review.standard-disclosures.v1"],
    )


def _build_wave_render_package(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
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
        report_data_contract_version="dpm_wave_report_input.v1",
        template_id="rebalance-wave",
        report_data=report_data,
        lineage_refs=_dpm_lineage_refs(
            job.job_id,
            report_data["wave_id"],
            report_data["content_hash"],
            portfolio_memory,
        ),
        disclosure_refs=["rebalance-wave.standard-disclosures.v1"],
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
