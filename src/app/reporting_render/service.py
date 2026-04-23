from __future__ import annotations

from decimal import Decimal, InvalidOperation
from functools import lru_cache
from numbers import Real
from typing import Any, Protocol

from app.clients.render_client import RenderClient
from app.config import settings
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.service import get_report_input_snapshot_store


class RenderSnapshotStore(Protocol):
    def get_snapshot_by_job(self, report_job_id: str) -> Any: ...


class RenderJobLedger(Protocol):
    def mark_rendering(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        render_job_id: str,
        output_format: str,
        template_id: str,
        template_version: str,
    ) -> ReportJobLedgerRecord: ...

    def mark_completed(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        render_job_id: str,
        output_format: str,
        template_id: str,
        template_version: str,
        artifact_sha256: str | None,
        bounded_determinism_fingerprint: str | None,
        runtime_engine: str | None,
        runtime_engine_version: str | None,
        render_duration_ms: int | None,
    ) -> ReportJobLedgerRecord: ...

    def mark_failed(
        self,
        *,
        job_id: str,
        actor: str,
        correlation_id: str,
        trace_id: str,
        failure_category: str,
        failure_message: str,
        retry_eligible: bool,
    ) -> ReportJobLedgerRecord: ...


class PortfolioReviewRenderOrchestrationService:
    def __init__(
        self,
        *,
        render_client: RenderClient,
        snapshot_store: RenderSnapshotStore,
        job_ledger: RenderJobLedger,
    ) -> None:
        self._render_client = render_client
        self._snapshot_store = snapshot_store
        self._job_ledger = job_ledger

    async def render_for_job(self, job: ReportJobLedgerRecord) -> ReportJobLedgerRecord:
        if "pdf" not in job.requested_output_formats:
            return job
        if job.status in {"completed", "completed_with_warnings", "failed", "cancelled"}:
            return job
        if job.status == "rendering":
            return job
        if job.status != "data_ready":
            return job

        snapshot = self._snapshot_store.get_snapshot_by_job(job.job_id)
        render_job_id = job.render_job_id or f"rdr_{job.job_id}_pdf"
        payload = _build_render_package(
            job=job,
            snapshot=snapshot.snapshot_payload,
            render_job_id=render_job_id,
        )
        self._job_ledger.mark_rendering(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            render_job_id=render_job_id,
            output_format="pdf",
            template_id="portfolio-review",
            template_version="v1",
        )

        status_code, response_payload = await self._render_client.submit_render_package(
            payload,
            correlation_id=job.correlation_id,
        )
        if status_code in {200, 201} and response_payload.get("status") == "rendered":
            return self._job_ledger.mark_completed(
                job_id=job.job_id,
                actor=job.triggered_by,
                correlation_id=job.correlation_id,
                trace_id=job.trace_id,
                render_job_id=str(response_payload.get("render_job_id") or render_job_id),
                output_format="pdf",
                template_id=str(response_payload.get("template_id") or "portfolio-review"),
                template_version=str(response_payload.get("template_version") or "v1"),
                artifact_sha256=_optional_str(response_payload.get("artifact_sha256")),
                bounded_determinism_fingerprint=_optional_str(
                    response_payload.get("bounded_determinism_fingerprint")
                ),
                runtime_engine=_optional_str(response_payload.get("runtime_engine")),
                runtime_engine_version=_optional_str(
                    response_payload.get("runtime_engine_version")
                ),
                render_duration_ms=_optional_int(response_payload.get("render_duration_ms")),
            )

        detail = response_payload.get("detail")
        detail_payload = detail if isinstance(detail, dict) else {}
        failure_code = str(detail_payload.get("code") or "")
        failure_message = _optional_str(detail_payload.get("message")) or _optional_str(
            response_payload.get("failure_message")
        )
        failure_category = "render_execution_failed"
        retry_eligible = status_code >= 500
        if status_code == 409 or failure_code == "render_job_conflict":
            failure_category = "render_conflict"
            retry_eligible = False
        elif status_code == 422 or failure_code == "render_package_invalid":
            failure_category = "render_validation_failed"
            retry_eligible = False
        return self._job_ledger.mark_failed(
            job_id=job.job_id,
            actor=job.triggered_by,
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
            failure_category=failure_category,
            failure_message=failure_message or "lotus-render execution failed.",
            retry_eligible=retry_eligible,
        )


def _build_render_package(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
) -> dict[str, Any]:
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
        "performance_periods": _performance_periods(snapshot),
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


def _top_holdings(snapshot: dict[str, Any]) -> list[dict[str, str]]:
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
                    "market_value": _decimal_text(item.get("market_value_reporting_currency")),
                    "unrealized_pnl": _decimal_text(item.get("unrealized_pnl_reporting_currency")),
                    "ytd_contribution_pct": _percent_text(item.get("ytd_contribution_pct")),
                    "_sort_value": _optional_decimal(item.get("market_value_reporting_currency"))
                    or Decimal("0"),
                }
            )
    flattened.sort(key=lambda item: item["_sort_value"], reverse=True)
    top_holdings: list[dict[str, str]] = []
    for item in flattened[:5]:
        top_holdings.append(
            {
                "asset_class": item["asset_class"],
                "security_name": item["security_name"],
                "weight_pct": item["weight_pct"],
                "market_value": item["market_value"],
                "unrealized_pnl": item["unrealized_pnl"],
                "ytd_contribution_pct": item["ytd_contribution_pct"],
            }
        )
    return top_holdings


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


@lru_cache(maxsize=1)
def get_portfolio_review_render_orchestration_service() -> (
    PortfolioReviewRenderOrchestrationService
):
    return PortfolioReviewRenderOrchestrationService(
        render_client=RenderClient(
            base_url=settings.render_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        ),
        snapshot_store=get_report_input_snapshot_store(),
        job_ledger=get_report_job_ledger(),
    )
