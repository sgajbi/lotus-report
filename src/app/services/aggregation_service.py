from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from app.application_errors import ReportingNotFoundError, ReportingUpstreamError
from app.clients.core_query_client import CoreQueryClient
from app.clients.performance_client import PerformanceClient
from app.config import settings
from app.models.contracts import (
    AggregationRow,
    AggregationScope,
    PortfolioAggregationResponse,
    UnavailableSource,
)
from app.precision_policy import quantize_money, quantize_performance, quantize_quantity, to_decimal


@dataclass(frozen=True)
class _AggregationInputs:
    """What each upstream returned, and which ones did not answer."""

    summary: dict[str, Any]
    allocation: dict[str, Any]
    performance: dict[str, Any]
    unavailable: list[UnavailableSource] = field(default_factory=list)


class AggregationService:
    def __init__(
        self,
        core_query_client: CoreQueryClient | None = None,
        performance_client: PerformanceClient | None = None,
    ):
        self._core_query_client = core_query_client or CoreQueryClient(
            base_url=settings.core_query_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        )
        self._performance_client = performance_client or PerformanceClient(
            base_url=settings.performance_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        )

    async def _fetch_inputs(
        self, portfolio_id: str, as_of_date: date, *, admitted_tenant_id: str
    ) -> _AggregationInputs:
        """Fetch the upstream contracts, recording what did not answer.

        Every `status >= 400` used to collapse into `{}`, which erased the
        difference between "the portfolio holds nothing" and "the source
        refused us". Downstream then substituted constants for the missing
        values, so an upstream 401, 403, 404 or 503 all produced the same
        confident answer. Absence is now carried forward as absence.
        """
        unavailable: list[UnavailableSource] = []

        summary_status, summary_payload = await self._core_query_client.get_portfolio_summary(
            portfolio_id=portfolio_id,
            payload={"as_of_date": as_of_date.isoformat()},
            admitted_tenant_id=admitted_tenant_id,
        )
        if summary_status >= 400:
            # The summary carries the market value and the position count, so
            # every row in this response depends on it. Refusing is the only
            # truthful outcome; a 404 is the caller's answer, anything else is
            # ours to report as an upstream failure.
            detail = {
                "code": "aggregation_source_unavailable",
                "message": "lotus-core did not return a portfolio summary.",
                "service": "lotus-core",
                "endpoint": "/reporting/portfolio-summary/query",
                "status_code": summary_status,
            }
            if summary_status == 404:
                raise ReportingNotFoundError(detail)
            raise ReportingUpstreamError(detail)

        allocation_status, allocation_payload = await self._core_query_client.get_asset_allocation(
            portfolio_id=portfolio_id,
            payload={"as_of_date": as_of_date.isoformat(), "dimensions": ["asset_class"]},
            admitted_tenant_id=admitted_tenant_id,
        )
        if allocation_status != 200:
            # Allocation only adds per-asset-class rows. Its absence loses
            # those rows and nothing else, so it is reported rather than
            # refused -- but it IS reported, because an empty allocation and an
            # unfetched one are different facts.
            allocation_payload = {}
            unavailable.append(
                UnavailableSource(
                    service="lotus-core",
                    endpoint="/reporting/asset-allocation/query",
                    status_code=allocation_status,
                    reason="no_response",
                )
            )

        (
            performance_status,
            performance_payload,
        ) = await self._performance_client.get_workspace_summary(
            {
                "portfolio_id": portfolio_id,
                "report_end_date": as_of_date.isoformat(),
                "input_mode": "stateful",
                "stateful_input": {},
                "periods": [{"period": "YTD", "frequencies": ["daily"]}],
            },
            admitted_tenant_id=admitted_tenant_id,
        )
        if performance_status != 200:
            # A 202 is the accepted envelope returned after the client's polling
            # budget is exhausted: the calculation is still running. Guarding on
            # `>= 400` treated that as a completed response carrying no return,
            # so the row was dropped and `unavailable_sources` stayed empty --
            # exactly the empty-versus-unavailable ambiguity this change exists
            # to remove, reintroduced one status code to the left.
            performance_payload = {}
            unavailable.append(
                UnavailableSource(
                    service="lotus-performance",
                    endpoint="/performance/workspace-summary",
                    status_code=performance_status,
                    reason="pending" if performance_status < 400 else "no_response",
                )
            )

        return _AggregationInputs(
            summary=summary_payload if isinstance(summary_payload, dict) else {},
            allocation=allocation_payload if isinstance(allocation_payload, dict) else {},
            performance=performance_payload if isinstance(performance_payload, dict) else {},
            unavailable=unavailable,
        )

    def _build_asset_class_rows(
        self, core_query_payload: dict[str, Any], total_mv: float
    ) -> list[AggregationRow]:
        allocation = core_query_payload.get("allocation", {})
        if not isinstance(allocation, dict):
            return []
        views = allocation.get("views", [])
        if not isinstance(views, list):
            return []
        asset_class_view = None
        for view in views:
            if not isinstance(view, dict):
                continue
            if str(view.get("dimension", "")).lower() == "asset_class":
                asset_class_view = view
                break
        if not isinstance(asset_class_view, dict):
            return []
        buckets = asset_class_view.get("buckets", [])
        if not isinstance(buckets, list):
            return []

        rows: list[AggregationRow] = []
        for bucket in buckets:
            if not isinstance(bucket, dict):
                continue
            asset_class = bucket.get("dimension_value")
            if not isinstance(asset_class, str) or not asset_class.strip():
                continue
            try:
                asset_market_value = float(
                    quantize_money(bucket.get("market_value_reporting_currency"))
                )
            except (TypeError, ValueError):
                continue
            if asset_market_value <= 0 or total_mv <= 0:
                continue
            weight = bucket.get("weight")
            if weight is None:
                weight_pct = float(
                    quantize_performance(
                        (to_decimal(asset_market_value) / to_decimal(total_mv)) * 100
                    )
                )
            else:
                try:
                    weight_pct = float(quantize_performance(to_decimal(weight) * 100))
                except (TypeError, ValueError):
                    weight_pct = float(
                        quantize_performance(
                            (to_decimal(asset_market_value) / to_decimal(total_mv)) * 100
                        )
                    )
            rows.append(
                AggregationRow(
                    bucket=str(asset_class).upper(),
                    metric="weight_pct",
                    value=weight_pct,
                )
            )

        rows.sort(key=lambda row: row.bucket)
        return rows

    async def get_portfolio_aggregation_live(
        self,
        portfolio_id: str,
        as_of_date: date,
        *,
        admitted_tenant_id: str,
    ) -> PortfolioAggregationResponse:
        """Aggregate what the upstreams actually returned, and nothing else.

        Each row is emitted only when its source supplied the value. The
        previous form substituted `1_250_000.0` for an absent market value and
        `0` for an absent position count and return, so a portfolio whose
        sources had all refused reported a quarter of a million in assets --
        measured on 401, 403, 404 and 503 alike.

        A zero that the source actually reported is still emitted. That is the
        distinction the constants destroyed: `position_count = 0` is a fact
        about an empty portfolio, and its absence is a fact about an unanswered
        question.
        """
        scope = AggregationScope(portfolio_id=portfolio_id, as_of_date=as_of_date)
        inputs = await self._fetch_inputs(
            portfolio_id, as_of_date, admitted_tenant_id=admitted_tenant_id
        )

        totals = inputs.summary.get("totals", {})
        if not isinstance(totals, dict):
            totals = {}
        snapshot_metadata = inputs.summary.get("snapshot_metadata", {})
        if not isinstance(snapshot_metadata, dict):
            snapshot_metadata = {}

        total_mv = totals.get("total_market_value_reporting_currency")
        if total_mv is None:
            # A 200 that carries no total is a contract violation, not an empty
            # portfolio: the field is how the summary states a portfolio worth
            # nothing. Refusing beats inventing a number for it.
            raise ReportingUpstreamError(
                {
                    "code": "aggregation_source_incomplete",
                    "message": (
                        "lotus-core returned a portfolio summary without "
                        "total_market_value_reporting_currency."
                    ),
                    "service": "lotus-core",
                    "endpoint": "/reporting/portfolio-summary/query",
                }
            )

        try:
            market_value = float(quantize_money(total_mv))
        except (TypeError, ValueError, ArithmeticError) as exc:
            # A 200 carrying `"n/a"` is an invalid upstream contract, not a
            # server fault of ours. Letting the conversion raise produced an
            # unstructured 500 that named nothing, because the router catches
            # only `ReportingApplicationError`.
            raise ReportingUpstreamError(
                {
                    "code": "aggregation_source_incomplete",
                    "message": (
                        "lotus-core returned a non-numeric total_market_value_reporting_currency."
                    ),
                    "service": "lotus-core",
                    "endpoint": "/reporting/portfolio-summary/query",
                }
            ) from exc
        rows = [
            AggregationRow(bucket="TOTAL", metric="market_value_base", value=market_value),
        ]

        # This endpoint always reports a position count, so a summary that
        # omits or malforms it answered incompletely. Dropping the row silently
        # left `unavailable_sources` empty and recreated the ambiguity: a
        # consumer could not tell malformed evidence from a metric this
        # response simply does not carry.
        position_count = self._optional_int(snapshot_metadata.get("position_count"))
        if position_count is None:
            inputs.unavailable.append(
                UnavailableSource(
                    service="lotus-core",
                    endpoint="/reporting/portfolio-summary/query",
                    status_code=200,
                    reason="incomplete_payload",
                )
            )
        if position_count is not None:
            rows.append(
                AggregationRow(
                    bucket="TOTAL",
                    metric="position_count",
                    value=float(quantize_quantity(position_count)),
                )
            )

        ytd_return = self._ytd_return(inputs.performance)
        if ytd_return is not None:
            rows.append(
                AggregationRow(
                    bucket="TOTAL",
                    metric="return_ytd_pct",
                    value=float(quantize_performance(ytd_return)),
                )
            )

        rows.extend(
            self._build_asset_class_rows(
                core_query_payload={"allocation": inputs.allocation},
                total_mv=market_value,
            )
        )
        return PortfolioAggregationResponse(
            scope=scope,
            generated_at=datetime.now(UTC),
            rows=rows,
            unavailable_sources=inputs.unavailable,
        )

    @staticmethod
    def _optional_int(value: object) -> int | None:
        """An int the source actually supplied, or None. Never a substituted 0."""
        if value is None:
            return None
        # `bool` first: it is an `int` subclass, so True would otherwise be
        # admitted as a position count of 1.
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return None
        return None

    @staticmethod
    def _ytd_return(performance_payload: dict[str, Any]) -> object | None:
        results = performance_payload.get("results_by_period")
        if not isinstance(results, dict):
            return None
        cursor: object = results
        for key in ("YTD", "portfolio_twr", "net", "summary", "cumulative_return", "base"):
            if not isinstance(cursor, dict):
                return None
            cursor = cursor.get(key)
            if cursor is None:
                return None
        return cursor
