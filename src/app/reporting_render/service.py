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
        "allocation_breakdowns": _allocation_breakdowns(snapshot),
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
