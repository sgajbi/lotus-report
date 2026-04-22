from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.clients.core_query_client import CoreQueryClient
from app.clients.performance_client import PerformanceClient
from app.clients.risk_client import RiskClient
from app.config import settings

AS_OF_DATE_KEYS = ("as_of_date", "asOfDate")
REPORTING_CURRENCY_KEYS = ("reporting_currency", "reportingCurrency")
LOOK_THROUGH_MODE_KEYS = ("look_through_mode", "lookThroughMode")
ALLOCATION_DIMENSIONS_KEYS = ("allocation_dimensions", "allocationDimensions")
BENCHMARK_CODE_KEYS = ("benchmark_code", "benchmarkCode")
RISK_METRICS = ("VOLATILITY", "SHARPE", "DRAWDOWN", "VAR")
REVIEW_SECTION_DEFINITIONS = (
    ("OVERVIEW", "executive_summary", "Executive Review Summary", "overview"),
    ("ALLOCATION", "asset_allocation", "Asset Allocation And Portfolio Construction", "allocation"),
    ("PERFORMANCE", "performance_review", "Performance Review", "performance"),
    ("RISK_ANALYTICS", "risk_review", "Risk Review", "riskAnalytics"),
    (
        "INCOME_AND_ACTIVITY",
        "income_cash_activity",
        "Income, Cash, And Activity",
        "incomeAndActivity",
    ),
    ("HOLDINGS", "holdings_appendix", "Holdings Appendix", "holdings"),
    ("TRANSACTIONS", "transactions_appendix", "Transactions Appendix", "transactions"),
)


class ReportingReadService:
    def __init__(
        self,
        core_query_client: CoreQueryClient | None = None,
        performance_client: PerformanceClient | None = None,
        risk_client: RiskClient | None = None,
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
        self._risk_client = risk_client or RiskClient(
            base_url=settings.risk_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        )

    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        request_payload: dict[str, object],
        correlation_id: str | None,
    ) -> dict[str, object]:
        as_of_date = self._request_as_of_date(request_payload)
        requested_sections = self._requested_sections(
            request_payload=request_payload,
            default_sections=["WEALTH", "ALLOCATION", "PNL", "INCOME", "ACTIVITY"],
        )

        summary_request: dict[str, object] = {"as_of_date": as_of_date}
        self._copy_optional_request_string(
            summary_request,
            request_payload,
            source_keys=REPORTING_CURRENCY_KEYS,
            target_key="reporting_currency",
        )

        status_code, payload = await self._core_query_client.get_portfolio_summary(
            portfolio_id=portfolio_id,
            payload=summary_request,
            correlation_id=correlation_id,
        )
        summary = self._unwrap_core_query_summary(status_code=status_code, payload=payload)

        ytd_start = f"{as_of_date[:4]}-01-01"
        response: dict[str, object] = {
            "scope": {
                "portfolio_id": portfolio_id,
                "as_of_date": as_of_date,
                "period_start_date": ytd_start,
                "period_end_date": as_of_date,
            }
        }
        if "WEALTH" in requested_sections:
            totals = self._as_dict(summary.get("totals"))
            response["wealth"] = {
                "total_market_value": self._to_float(
                    totals.get("total_market_value_reporting_currency")
                ),
                "total_cash": self._to_float(totals.get("cash_balance_reporting_currency")),
            }

        if "ALLOCATION" in requested_sections:
            allocation_request = self._build_allocation_request(request_payload)
            (
                allocation_status,
                allocation_payload,
            ) = await self._core_query_client.get_asset_allocation(
                portfolio_id=portfolio_id,
                payload=allocation_request,
                correlation_id=correlation_id,
            )
            allocation_response = self._unwrap_core_query_allocation(
                status_code=allocation_status,
                payload=allocation_payload,
            )
            response["allocation"] = self._map_allocation_views(
                self._as_list(allocation_response.get("views"))
            )

        if "INCOME" in requested_sections or "ACTIVITY" in requested_sections:
            transaction_params = self._build_transaction_window_params(request_payload)
            transaction_rows = await self._list_transaction_rows(
                portfolio_id=portfolio_id,
                correlation_id=correlation_id,
                params=transaction_params,
            )
            if "INCOME" in requested_sections:
                response["incomeSummary"] = self._map_income_summary_from_rows(transaction_rows)
            if "ACTIVITY" in requested_sections:
                response["activitySummary"] = self._map_activity_summary_from_rows(transaction_rows)

        if "PNL" in requested_sections:
            response["pnlSummary"] = self._map_pnl_summary(summary)
        return response

    async def get_portfolio_review(
        self,
        portfolio_id: str,
        request_payload: dict[str, object],
        correlation_id: str | None,
    ) -> dict[str, object]:
        as_of_date = self._request_as_of_date(request_payload)
        requested_sections = self._requested_sections(
            request_payload=request_payload,
            default_sections=[
                "OVERVIEW",
                "ALLOCATION",
                "PERFORMANCE",
                "RISK_ANALYTICS",
                "INCOME_AND_ACTIVITY",
                "HOLDINGS",
                "TRANSACTIONS",
            ],
        )

        summary_status, summary_payload = await self._core_query_client.get_portfolio_summary(
            portfolio_id=portfolio_id,
            payload={"as_of_date": as_of_date},
            correlation_id=correlation_id,
        )
        summary = self._unwrap_core_query_summary(
            status_code=summary_status, payload=summary_payload
        )
        response: dict[str, object] = self._new_review_response(
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
        )
        response["methodology"] = self._review_methodology(request_payload)
        transaction_rows: list[dict[str, object]] | None = None

        if "OVERVIEW" in requested_sections:
            response["overview"] = self._map_review_overview(summary)
        if "ALLOCATION" in requested_sections:
            allocation_request = self._build_allocation_request(request_payload)
            (
                allocation_status,
                allocation_payload,
            ) = await self._core_query_client.get_asset_allocation(
                portfolio_id=portfolio_id,
                payload=allocation_request,
                correlation_id=correlation_id,
            )
            allocation_response = self._unwrap_core_query_allocation(
                status_code=allocation_status,
                payload=allocation_payload,
            )
            response["allocation"] = self._map_allocation_views(
                self._as_list(allocation_response.get("views"))
            )
        if "INCOME_AND_ACTIVITY" in requested_sections:
            if transaction_rows is None:
                transaction_rows = await self._list_transaction_rows(
                    portfolio_id=portfolio_id,
                    correlation_id=correlation_id,
                    params=self._build_transaction_window_params(request_payload),
                )
            response["incomeAndActivity"] = {
                "incomeSummary": self._map_income_summary_from_rows(transaction_rows),
                "activitySummary": self._map_activity_summary_from_rows(transaction_rows),
            }
        if "HOLDINGS" in requested_sections:
            (
                positions_status,
                positions_payload,
            ) = await self._core_query_client.get_portfolio_positions(
                portfolio_id=portfolio_id,
                params=self._build_position_params(request_payload),
                correlation_id=correlation_id,
            )
            response["holdings"] = self._unwrap_core_query_positions(
                status_code=positions_status,
                payload=positions_payload,
            )
        if "TRANSACTIONS" in requested_sections:
            if transaction_rows is None:
                transaction_rows = await self._list_transaction_rows(
                    portfolio_id=portfolio_id,
                    correlation_id=correlation_id,
                    params=self._build_transaction_window_params(request_payload),
                )
            response["transactions"] = self._map_review_transactions(transaction_rows)

        workspace_summary_payload: dict[str, object] | None = None
        if "PERFORMANCE" in requested_sections:
            (
                performance_status,
                performance_payload,
            ) = await self._performance_client.get_workspace_summary(
                self._build_workspace_summary_request(
                    portfolio_id=portfolio_id,
                    as_of_date=as_of_date,
                    request_payload=request_payload,
                    periods=["MTD", "QTD", "YTD", "THREE_YEAR", "SI"],
                )
            )
            if performance_status < status.HTTP_400_BAD_REQUEST:
                workspace_summary_payload = performance_payload
            if performance_status < status.HTTP_400_BAD_REQUEST:
                response["performance"] = self._map_workspace_performance(
                    performance_payload,
                    request_payload=request_payload,
                )
            else:
                response["performance"] = None

        if "RISK_ANALYTICS" in requested_sections:
            response["riskAnalytics"] = await self._build_risk_analytics(
                portfolio_id=portfolio_id,
                as_of_date=as_of_date,
                request_payload=request_payload,
                workspace_summary_payload=workspace_summary_payload,
            )

        client_sections = self._build_client_sections(
            response=response,
            requested_sections=requested_sections,
        )
        response["readiness"] = self._review_readiness(client_sections=client_sections)
        response["client_sections"] = client_sections
        response["advisor_sections"] = []
        return response

    def _new_review_response(self, *, portfolio_id: str, as_of_date: str) -> dict[str, object]:
        return {
            "contract_version": settings.contract_version,
            "report_id": f"portfolio-review:{portfolio_id}:{as_of_date}",
            "portfolio_id": portfolio_id,
            "as_of_date": as_of_date,
            "generated_at": datetime.now(timezone.utc),
            "readiness": {"status": "ready"},
            "methodology": self._review_methodology({}),
        }

    def _review_readiness(
        self,
        *,
        client_sections: list[dict[str, object]],
    ) -> dict[str, object]:
        unavailable_sections = [
            self._safe_str(section.get("title"))
            for section in client_sections
            if section.get("status") == "unavailable"
        ]

        if not unavailable_sections:
            return {"status": "ready"}
        return {
            "status": "partial",
            "reason": (
                "Unavailable sections for the selected request: " + ", ".join(unavailable_sections)
            ),
        }

    def _build_client_sections(
        self,
        *,
        response: dict[str, object],
        requested_sections: set[str],
    ) -> list[dict[str, object]]:
        return [
            self._build_review_section(
                requested_key=requested_key,
                section_id=section_id,
                title=title,
                response_key=response_key,
                response=response,
                requested_sections=requested_sections,
            )
            for requested_key, section_id, title, response_key in REVIEW_SECTION_DEFINITIONS
        ]

    def _build_review_section(
        self,
        *,
        requested_key: str,
        section_id: str,
        title: str,
        response_key: str,
        response: dict[str, object],
        requested_sections: set[str],
    ) -> dict[str, object]:
        if requested_key not in requested_sections:
            return {
                "section_id": section_id,
                "title": title,
                "status": "omitted_by_request",
                "reason_code": "section_not_requested",
                "message": f"{title} was not requested.",
                "items": [],
            }

        section_payload = response.get(response_key)
        if section_payload is None:
            return {
                "section_id": section_id,
                "title": title,
                "status": "unavailable",
                "reason_code": "source_unavailable",
                "message": f"{title} is unavailable for this request.",
                "items": [],
            }
        supportability = self._as_dict(self._as_dict(section_payload).get("supportability"))
        supportability_status = supportability.get("status")
        if supportability_status in {"partial", "unavailable"}:
            return {
                "section_id": section_id,
                "title": title,
                "status": supportability_status,
                "reason_code": self._supportability_reason_code(supportability),
                "message": self._supportability_message(supportability, title),
                "items": [self._as_dict(section_payload)],
            }

        return {
            "section_id": section_id,
            "title": title,
            "status": "ready",
            "items": [self._as_dict(section_payload)],
        }

    async def _build_risk_analytics(
        self,
        portfolio_id: str,
        as_of_date: str,
        request_payload: dict[str, object],
        workspace_summary_payload: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if workspace_summary_payload is None:
            (
                summary_status,
                workspace_summary_payload,
            ) = await self._performance_client.get_workspace_summary(
                self._build_workspace_summary_request(
                    portfolio_id=portfolio_id,
                    as_of_date=as_of_date,
                    request_payload=request_payload,
                    periods=["YTD", "THREE_YEAR", "SI"],
                )
            )
            if summary_status >= status.HTTP_400_BAD_REQUEST:
                return self._risk_unavailable(
                    reason_code="risk_return_history_unavailable",
                    message=(
                        "Risk Review is unavailable because performance return history could "
                        "not be sourced."
                    ),
                )

        returns = self._extract_daily_returns_from_workspace_summary(workspace_summary_payload)
        if not returns:
            return self._risk_unavailable(
                reason_code="missing_return_history",
                message=(
                    "Risk Review is unavailable because no daily return history was available "
                    "for the selected request."
                ),
            )
        portfolio_open_date = self._workspace_portfolio_open_date(workspace_summary_payload)
        if portfolio_open_date is None:
            return self._risk_unavailable(
                reason_code="missing_return_history",
                message=(
                    "Risk Review is unavailable because the sourced return history did not "
                    "include a portfolio open date."
                ),
            )

        risk_payload = {
            "scope": {"asOfDate": as_of_date, "netOrGross": "NET"},
            "periods": [{"type": "YTD"}, {"type": "THREE_YEAR"}],
            "metrics": list(RISK_METRICS),
            "portfolioOpenDate": portfolio_open_date,
            "returns": returns,
            "benchmarkReturns": [],
        }
        risk_status, risk_response = await self._risk_client.calculate_risk(risk_payload)
        if risk_status >= status.HTTP_400_BAD_REQUEST:
            return self._risk_unavailable(
                reason_code="risk_upstream_failure",
                message=(
                    "Risk Review is unavailable because lotus-risk could not calculate metrics."
                ),
            )

        results = self._as_dict(risk_response.get("results"))
        metadata = self._as_dict(risk_response.get("metadata"))
        supportability = self._risk_supportability(
            results=results,
            metadata=metadata,
            request_payload=request_payload,
        )
        return {
            "source": {
                "service": "lotus-risk",
                "endpoint": "/analytics/risk/calculate",
            },
            "methodology": {
                "metrics": list(RISK_METRICS),
                "return_source": "lotus-performance workspace summary",
                "return_basis": "NET",
                "benchmark_code": self._optional_string(request_payload, *BENCHMARK_CODE_KEYS),
            },
            "supportability": supportability,
            "summary": self._risk_metric_summary(results),
            "results": results,
            "metadata": metadata,
        }

    def _risk_unavailable(self, *, reason_code: str, message: str) -> dict[str, object]:
        return {
            "source": {
                "service": "lotus-risk",
                "endpoint": "/analytics/risk/calculate",
            },
            "methodology": {"metrics": list(RISK_METRICS), "return_basis": "NET"},
            "supportability": {
                "status": "unavailable",
                "notes": [
                    {
                        "code": reason_code,
                        "severity": "blocking",
                        "message": message,
                    }
                ],
            },
            "summary": {},
            "results": {},
            "metadata": {},
        }

    def _risk_supportability(
        self,
        *,
        results: dict[str, object],
        metadata: dict[str, object],
        request_payload: dict[str, object],
    ) -> dict[str, object]:
        notes: list[dict[str, object]] = []
        if not results:
            notes.append(
                {
                    "code": "missing_return_history",
                    "severity": "blocking",
                    "message": "lotus-risk returned no period results for the selected request.",
                }
            )

        risk_free_context = self._as_dict(metadata.get("risk_free_context"))
        if risk_free_context.get("requested") and risk_free_context.get("reason") == "ZERO_RATE":
            notes.append(
                {
                    "code": "missing_risk_free_rate",
                    "severity": "informational",
                    "message": (
                        "Risk-adjusted return uses the lotus-risk zero-rate convention because "
                        "no source-backed risk-free rate was applied."
                    ),
                }
            )

        benchmark_code = self._optional_string(request_payload, *BENCHMARK_CODE_KEYS)
        benchmark_context = self._as_dict(metadata.get("benchmark_context"))
        if benchmark_code is None:
            notes.append(
                {
                    "code": "missing_benchmark",
                    "severity": "informational",
                    "message": (
                        "Benchmark-relative risk posture is unavailable because no benchmark "
                        "code was provided."
                    ),
                }
            )
        elif not benchmark_context.get("requested"):
            notes.append(
                {
                    "code": "missing_benchmark",
                    "severity": "informational",
                    "message": (
                        "Benchmark-relative risk posture is unavailable because benchmark "
                        "return series is not sourced for the risk calculation."
                    ),
                }
            )

        status_value = (
            "unavailable" if any(note.get("severity") == "blocking" for note in notes) else "ready"
        )
        return {"status": status_value, "notes": notes}

    def _risk_metric_summary(self, results: dict[str, object]) -> dict[str, object]:
        summary: dict[str, object] = {}
        for period, period_payload in results.items():
            row = self._as_dict(period_payload)
            metrics = self._as_dict(row.get("metrics"))
            summary[period] = {
                "volatility": self._metric_value(metrics, "VOLATILITY"),
                "risk_adjusted_return": self._metric_value(metrics, "SHARPE"),
                "drawdown": self._metric_value(metrics, "DRAWDOWN"),
                "value_at_risk": self._metric_value(metrics, "VAR"),
            }
        return summary

    def _metric_value(self, metrics: dict[str, object], metric_name: str) -> object | None:
        metric = self._as_dict(metrics.get(metric_name))
        return metric.get("value")

    def _supportability_reason_code(self, supportability: dict[str, object]) -> str:
        for note in self._as_list(supportability.get("notes")):
            note_payload = self._as_dict(note)
            code = note_payload.get("code")
            if isinstance(code, str) and code:
                return code
        return "source_unavailable"

    def _supportability_message(self, supportability: dict[str, object], title: str) -> str:
        for note in self._as_list(supportability.get("notes")):
            note_payload = self._as_dict(note)
            message = note_payload.get("message")
            if isinstance(message, str) and message:
                return message
        return f"{title} is unavailable for this request."

    def _extract_daily_returns_from_workspace_summary(
        self,
        workspace_payload: dict[str, object],
    ) -> list[dict[str, object]]:
        results_by_period = self._as_dict(workspace_payload.get("results_by_period"))
        daily_items: list[object] = []
        for period_name in ("THREE_YEAR", "SI", "YTD", "QTD", "MTD"):
            period_payload = self._as_dict(results_by_period.get(period_name))
            portfolio_twr = self._as_dict(period_payload.get("portfolio_twr"))
            net_block = self._as_dict(portfolio_twr.get("net"))
            breakdowns = self._as_dict(net_block.get("breakdowns"))
            candidate_items = breakdowns.get("daily")
            if isinstance(candidate_items, list) and candidate_items:
                daily_items = candidate_items
                break
        if not daily_items:
            return []

        returns: list[dict[str, object]] = []
        for item in daily_items:
            if not isinstance(item, dict):
                continue
            period_end = item.get("period_end")
            period = item.get("period")
            return_value = self._as_dict(item.get("period_return")).get("base")
            if not isinstance(return_value, (int, float)):
                continue
            if isinstance(period_end, str) and period_end:
                return_date = period_end
            elif isinstance(period, str) and period:
                return_date = period[:10]
            else:
                continue
            returns.append({"date": return_date, "value": float(return_value)})
        return returns

    def _unwrap_core_query_summary(
        self, status_code: int, payload: dict[str, object]
    ) -> dict[str, object]:
        if status_code < status.HTTP_400_BAD_REQUEST:
            if isinstance(payload, dict) and {"portfolio_id", "totals", "snapshot_metadata"} <= set(
                payload
            ):
                return payload
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="lotus-core portfolio summary payload missing required fields.",
            )
        if status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=payload.get("detail"))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"lotus-core portfolio summary upstream failure: {payload}",
        )

    def _unwrap_core_query_allocation(
        self, status_code: int, payload: dict[str, object]
    ) -> dict[str, object]:
        if status_code < status.HTTP_400_BAD_REQUEST:
            if isinstance(payload, dict) and "views" in payload:
                return payload
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="lotus-core asset allocation payload missing required fields.",
            )
        if status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=payload.get("detail"))
        if status_code == status.HTTP_422_UNPROCESSABLE_CONTENT:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=payload.get("detail")
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"lotus-core asset allocation upstream failure: {payload}",
        )

    def _unwrap_core_query_positions(
        self, status_code: int, payload: dict[str, object]
    ) -> dict[str, object]:
        if status_code < status.HTTP_400_BAD_REQUEST:
            if isinstance(payload, dict) and "positions" in payload:
                return self._map_holdings_from_positions(payload)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="lotus-core positions payload missing required fields.",
            )
        if status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=payload.get("detail"))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"lotus-core positions upstream failure: {payload}",
        )

    def _map_review_overview(self, summary: dict[str, object]) -> dict[str, object]:
        totals = self._as_dict(summary.get("totals"))
        return {
            "total_market_value": self._to_float(
                totals.get("total_market_value_reporting_currency")
            ),
            "total_cash": self._to_float(totals.get("cash_balance_reporting_currency")),
            "invested_market_value": self._to_float(
                totals.get("invested_market_value_reporting_currency")
            ),
            "currency": self._safe_str(
                summary.get("reporting_currency")
                or self._as_dict(summary.get("snapshot_metadata")).get("reporting_currency")
            ),
        }

    def _map_pnl_summary(self, summary: dict[str, object]) -> dict[str, object]:
        totals = self._as_dict(summary.get("totals"))
        total_market_value = self._to_float(totals.get("total_market_value_reporting_currency"))
        invested_market_value = self._to_float(
            totals.get("invested_market_value_reporting_currency")
        )
        total_pnl = total_market_value - invested_market_value
        return {
            "invested_market_value_reporting_currency": invested_market_value,
            "unrealized_pnl_reporting_currency": total_pnl,
            "realized_pnl_reporting_currency": 0.0,
            "total_pnl": total_pnl,
        }

    def _map_holdings_from_positions(self, payload: dict[str, object]) -> dict[str, object]:
        holdings_by_asset_class: dict[str, list[dict[str, object]]] = {}
        for item in self._as_list(payload.get("positions")):
            row = self._as_dict(item)
            asset_class = self._safe_str(row.get("asset_class")) or "UNKNOWN"
            holdings_by_asset_class.setdefault(asset_class, []).append(
                {
                    "security_id": self._safe_str(row.get("security_id")),
                    "instrument_name": self._safe_str(
                        row.get("instrument_name") or row.get("description")
                    ),
                    "quantity": self._to_float(row.get("quantity")),
                    "market_value_reporting_currency": self._to_float(
                        row.get("market_value_reporting_currency")
                    ),
                    "weight": self._to_float(row.get("weight")),
                    "currency": self._safe_str(row.get("currency")),
                }
            )
        return {
            "holdingsByAssetClass": holdings_by_asset_class,
            "positionCount": self._to_int(payload.get("total"))
            or sum(len(rows) for rows in holdings_by_asset_class.values()),
        }

    def _map_review_transactions(self, rows: list[dict[str, object]]) -> dict[str, object]:
        transactions_by_asset_class: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            asset_class = self._safe_str(row.get("asset_class")) or "UNKNOWN"
            transactions_by_asset_class.setdefault(asset_class, []).append(
                {
                    "transaction_id": self._safe_str(row.get("transaction_id")),
                    "transaction_date": self._safe_str(row.get("transaction_date")),
                    "transaction_type": self._safe_str(row.get("transaction_type")),
                    "gross_transaction_amount_reporting_currency": self._to_float(
                        row.get("gross_transaction_amount_reporting_currency")
                    ),
                    "net_interest_amount_reporting_currency": self._to_float(
                        row.get("net_interest_amount_reporting_currency")
                    ),
                    "withholding_tax_amount_reporting_currency": self._to_float(
                        row.get("withholding_tax_amount_reporting_currency")
                    ),
                }
            )
        return {
            "transactionsByAssetClass": transactions_by_asset_class,
            "transactionCount": len(rows),
        }

    def _build_allocation_request(self, request_payload: dict[str, object]) -> dict[str, object]:
        dimensions = self._allocation_dimensions(request_payload)
        allocation_request: dict[str, object] = {"dimensions": dimensions}
        self._copy_optional_request_string(
            allocation_request,
            request_payload,
            source_keys=AS_OF_DATE_KEYS,
            target_key="as_of_date",
        )
        self._copy_optional_request_string(
            allocation_request,
            request_payload,
            source_keys=REPORTING_CURRENCY_KEYS,
            target_key="reporting_currency",
        )
        self._copy_optional_request_string(
            allocation_request,
            request_payload,
            source_keys=LOOK_THROUGH_MODE_KEYS,
            target_key="look_through_mode",
        )
        return allocation_request

    def _build_transaction_window_params(
        self, request_payload: dict[str, object]
    ) -> dict[str, object]:
        end_date = self._request_as_of_date(request_payload)
        transaction_params: dict[str, object] = {
            "start_date": f"{end_date[:4]}-01-01",
            "end_date": end_date,
            "sort_by": "transaction_date",
            "sort_order": "asc",
            "include_projected": "false",
            "limit": 500,
            "skip": 0,
        }
        self._copy_optional_request_string(
            transaction_params,
            request_payload,
            source_keys=AS_OF_DATE_KEYS,
            target_key="as_of_date",
        )
        self._copy_optional_request_string(
            transaction_params,
            request_payload,
            source_keys=REPORTING_CURRENCY_KEYS,
            target_key="reporting_currency",
        )
        return transaction_params

    def _build_position_params(self, request_payload: dict[str, object]) -> dict[str, object]:
        as_of_date = self._request_as_of_date(request_payload)
        params: dict[str, object] = {
            "as_of_date": as_of_date,
            "include_projected": "false",
        }
        self._copy_optional_request_string(
            params,
            request_payload,
            source_keys=REPORTING_CURRENCY_KEYS,
            target_key="reporting_currency",
        )
        return params

    def _allocation_dimensions(self, request_payload: dict[str, object]) -> list[str]:
        raw_dimensions = self._optional_value(request_payload, *ALLOCATION_DIMENSIONS_KEYS)
        if raw_dimensions is None:
            return ["asset_class"]
        if not isinstance(raw_dimensions, list) or not raw_dimensions:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="allocation_dimensions must be a non-empty list when provided.",
            )
        dimensions: list[str] = []
        supported_dimensions = {
            "asset_class",
            "currency",
            "sector",
            "country",
            "region",
            "product_type",
            "rating",
            "issuer",
        }
        for raw_dimension in raw_dimensions:
            if not isinstance(raw_dimension, str) or not raw_dimension.strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="allocation_dimensions cannot contain blank values.",
                )
            normalized = raw_dimension.strip().lower()
            if normalized not in supported_dimensions:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Unsupported allocation dimension: {raw_dimension}",
                )
            dimensions.append(normalized)
        return dimensions

    def _map_allocation_views(self, views: list[object]) -> dict[str, object]:
        allocation: dict[str, object] = {}
        for item in views:
            if not isinstance(item, dict):
                continue
            dimension = item.get("dimension")
            if not isinstance(dimension, str) or not dimension:
                continue
            buckets = []
            for bucket in self._as_list(item.get("buckets")):
                bucket_payload = self._as_dict(bucket)
                buckets.append(
                    {
                        "group": bucket_payload.get("dimension_value"),
                        "weight": self._to_float(bucket_payload.get("weight")),
                        "market_value": self._to_float(
                            bucket_payload.get("market_value_reporting_currency")
                        ),
                        "position_count": bucket_payload.get("position_count"),
                    }
                )
            allocation[self._allocation_view_key(dimension)] = buckets
        return allocation

    def _allocation_view_key(self, dimension: str) -> str:
        parts = [part for part in dimension.split("_") if part]
        if not parts:
            return "views"
        return "by" + "".join(part.capitalize() for part in parts)

    def _map_income_summary_from_rows(self, rows: list[dict[str, object]]) -> dict[str, object]:
        totals, _ = self._summarize_income_rows(rows)
        return totals

    def _map_activity_summary_from_rows(self, rows: list[dict[str, object]]) -> dict[str, object]:
        buckets = self._summarize_activity_rows(rows)
        summary: dict[str, object] = {}
        for bucket_name, bucket in buckets.items():
            normalized = bucket_name.lower()
            summary[f"total_{normalized}"] = self._to_float(bucket.get("amount_reporting_currency"))
            summary[f"{normalized}_transaction_count"] = self._to_int(
                bucket.get("transaction_count")
            )
        return summary

    async def _list_transaction_rows(
        self,
        *,
        portfolio_id: str,
        correlation_id: str | None,
        params: dict[str, object],
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        skip = 0
        limit = self._to_int(params.get("limit")) or 500

        while True:
            query_params = dict(params)
            query_params["skip"] = skip
            query_params["limit"] = limit
            status_code, payload = await self._core_query_client.get_portfolio_transactions(
                portfolio_id=portfolio_id,
                params=query_params,
                correlation_id=correlation_id,
            )
            if status_code < status.HTTP_400_BAD_REQUEST:
                if not isinstance(payload, dict) or "transactions" not in payload:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="lotus-core transactions payload missing required fields.",
                    )
                page_rows = [
                    item
                    for item in self._as_list(payload.get("transactions"))
                    if isinstance(item, dict)
                ]
                rows.extend(page_rows)
                total = self._to_int(payload.get("total"))
                skip += len(page_rows)
                if not page_rows or skip >= total:
                    break
                continue
            if status_code == status.HTTP_404_NOT_FOUND:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=payload.get("detail"),
                )
            if status_code == status.HTTP_422_UNPROCESSABLE_CONTENT:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=payload.get("detail"),
                )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"lotus-core transactions upstream failure: {payload}",
            )

        return rows

    def _summarize_income_rows(
        self,
        rows: list[dict[str, object]],
    ) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
        totals = self._new_income_metric()
        by_income_type: dict[str, dict[str, object]] = {}
        for row in rows:
            income_type = str(row.get("transaction_type") or "").strip().upper()
            if income_type not in {"DIVIDEND", "INTEREST"}:
                continue
            bucket = by_income_type.setdefault(income_type, self._new_income_metric())
            self._accumulate_income_metric(totals, row)
            self._accumulate_income_metric(bucket, row)
        return totals, by_income_type

    def _summarize_activity_rows(
        self,
        rows: list[dict[str, object]],
    ) -> dict[str, dict[str, object]]:
        buckets: dict[str, dict[str, object]] = {}
        for row in rows:
            transaction_type = str(row.get("transaction_type") or "").strip().upper()
            bucket_name = self._activity_bucket_name(transaction_type)
            if bucket_name is not None:
                bucket = buckets.setdefault(bucket_name, self._new_flow_metric())
                self._accumulate_flow_metric(
                    bucket,
                    reporting_amount=self._activity_reporting_amount(row),
                )
            withholding_reporting = self._absolute_money(
                row.get("withholding_tax_amount_reporting_currency")
            )
            if withholding_reporting > 0:
                tax_bucket = buckets.setdefault("TAXES", self._new_flow_metric())
                self._accumulate_flow_metric(
                    tax_bucket,
                    reporting_amount=withholding_reporting,
                )
        return buckets

    def _new_income_metric(self) -> dict[str, object]:
        return {
            "transaction_count": 0,
            "gross_amount_reporting_currency": 0.0,
            "withholding_tax_reporting_currency": 0.0,
            "other_deductions_reporting_currency": 0.0,
            "net_amount_reporting_currency": 0.0,
        }

    def _new_flow_metric(self) -> dict[str, object]:
        return {"transaction_count": 0, "amount_reporting_currency": 0.0}

    def _accumulate_income_metric(
        self,
        accumulator: dict[str, object],
        row: dict[str, object],
    ) -> None:
        accumulator["transaction_count"] = self._to_int(accumulator["transaction_count"]) + 1
        accumulator["gross_amount_reporting_currency"] = self._to_float(
            accumulator["gross_amount_reporting_currency"]
        ) + self._reporting_money(
            row,
            reporting_key="gross_transaction_amount_reporting_currency",
            portfolio_key="gross_transaction_amount",
        )
        accumulator["withholding_tax_reporting_currency"] = self._to_float(
            accumulator["withholding_tax_reporting_currency"]
        ) + self._reporting_money(
            row,
            reporting_key="withholding_tax_amount_reporting_currency",
            portfolio_key="withholding_tax_amount",
        )
        accumulator["other_deductions_reporting_currency"] = self._to_float(
            accumulator["other_deductions_reporting_currency"]
        ) + self._reporting_money(
            row,
            reporting_key="other_interest_deductions_amount_reporting_currency",
            portfolio_key="other_interest_deductions_amount",
        )
        accumulator["net_amount_reporting_currency"] = self._to_float(
            accumulator["net_amount_reporting_currency"]
        ) + self._income_net_reporting_amount(row)

    def _accumulate_flow_metric(
        self,
        accumulator: dict[str, object],
        *,
        reporting_amount: float,
    ) -> None:
        accumulator["transaction_count"] = self._to_int(accumulator["transaction_count"]) + 1
        accumulator["amount_reporting_currency"] = (
            self._to_float(accumulator["amount_reporting_currency"]) + reporting_amount
        )

    def _activity_bucket_name(self, transaction_type: str) -> str | None:
        if transaction_type in {"DEPOSIT", "TRANSFER_IN"}:
            return "INFLOWS"
        if transaction_type in {"WITHDRAWAL", "TRANSFER_OUT"}:
            return "OUTFLOWS"
        if transaction_type == "FEE":
            return "FEES"
        if transaction_type == "TAX":
            return "TAXES"
        return None

    def _activity_reporting_amount(self, row: dict[str, object]) -> float:
        if str(row.get("transaction_type") or "").strip().upper() == "FEE":
            return self._reporting_money(
                row,
                reporting_key="gross_transaction_amount_reporting_currency",
                portfolio_key="gross_transaction_amount",
            ) + self._reporting_money(
                row,
                reporting_key="trade_fee_reporting_currency",
                portfolio_key="trade_fee",
            )
        return self._reporting_money(
            row,
            reporting_key="gross_transaction_amount_reporting_currency",
            portfolio_key="gross_transaction_amount",
        )

    def _income_net_reporting_amount(self, row: dict[str, object]) -> float:
        if (
            str(row.get("transaction_type") or "").strip().upper() == "INTEREST"
            and row.get("net_interest_amount_reporting_currency") is not None
        ):
            return self._absolute_money(row.get("net_interest_amount_reporting_currency"))
        gross = self._reporting_money(
            row,
            reporting_key="gross_transaction_amount_reporting_currency",
            portfolio_key="gross_transaction_amount",
        )
        withholding = self._reporting_money(
            row,
            reporting_key="withholding_tax_amount_reporting_currency",
            portfolio_key="withholding_tax_amount",
        )
        other_deductions = self._reporting_money(
            row,
            reporting_key="other_interest_deductions_amount_reporting_currency",
            portfolio_key="other_interest_deductions_amount",
        )
        trade_fee = self._reporting_money(
            row,
            reporting_key="trade_fee_reporting_currency",
            portfolio_key="trade_fee",
        )
        return gross - withholding - other_deductions - trade_fee

    def _reporting_money(
        self,
        row: dict[str, object],
        *,
        reporting_key: str,
        portfolio_key: str,
    ) -> float:
        if row.get(reporting_key) is not None:
            return self._absolute_money(row.get(reporting_key))
        return self._absolute_money(row.get(portfolio_key))

    def _absolute_money(self, value: object) -> float:
        amount = self._to_float(value)
        return abs(amount)

    def _map_workspace_performance(
        self,
        payload: dict[str, object],
        request_payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        request_payload = request_payload or {}
        results_by_period = self._as_dict(payload.get("results_by_period"))
        summary: dict[str, object] = {}
        for period, row in results_by_period.items():
            row_dict = self._as_dict(row)
            portfolio_twr = self._as_dict(row_dict.get("portfolio_twr"))
            net_summary = self._as_dict(self._as_dict(portfolio_twr.get("net")).get("summary"))
            gross_summary = self._as_dict(self._as_dict(portfolio_twr.get("gross")).get("summary"))
            annualized_supported = self._annualized_return_supported(period)
            summary[period] = {
                "start_date": self._workspace_period_start(row_dict),
                "end_date": self._workspace_period_end(row_dict),
                "net_cumulative_return": self._return_base(net_summary, "cumulative_return"),
                "net_annualized_return": (
                    self._return_base(net_summary, "annualized_return")
                    if annualized_supported
                    else None
                ),
                "gross_cumulative_return": self._return_base(gross_summary, "cumulative_return"),
                "gross_annualized_return": (
                    self._return_base(gross_summary, "annualized_return")
                    if annualized_supported
                    else None
                ),
                "annualized_return_supported": annualized_supported,
            }
        return {
            "summary": summary,
            "benchmark": {
                "benchmark_code": self._optional_string(request_payload, *BENCHMARK_CODE_KEYS)
            },
            "methodology": self._review_methodology(request_payload),
        }

    def _annualized_return_supported(self, period: object) -> bool:
        return isinstance(period, str) and period.upper() in {"THREE_YEAR", "FIVE_YEAR", "SI"}

    def _review_methodology(self, request_payload: dict[str, object]) -> dict[str, object]:
        return {
            "performance_basis": "NET_AND_GROSS_WHERE_AVAILABLE",
            "benchmark_code": self._optional_string(request_payload, *BENCHMARK_CODE_KEYS),
            "fee_treatment": "source_provided",
            "return_methodology": "time_weighted_return",
            "annualization_policy": (
                "Sub-year annualized returns are suppressed unless source support is explicit."
            ),
        }

    def _build_workspace_summary_request(
        self,
        *,
        portfolio_id: str,
        as_of_date: str,
        request_payload: dict[str, object],
        periods: list[str],
    ) -> dict[str, object]:
        request: dict[str, object] = {
            "portfolio_id": portfolio_id,
            "report_end_date": as_of_date,
            "input_mode": "stateful",
            "stateful_input": {},
            "periods": [{"period": period, "frequencies": ["daily"]} for period in periods],
        }
        reporting_currency = self._optional_string(request_payload, *REPORTING_CURRENCY_KEYS)
        if reporting_currency:
            request["report_ccy"] = reporting_currency
            request["currency"] = reporting_currency
        return request

    def _workspace_period_start(self, period_payload: dict[str, object]) -> str | None:
        daily_breakdowns = self._workspace_daily_breakdowns(period_payload)
        if daily_breakdowns:
            period_start = self._as_dict(daily_breakdowns[0]).get("period_start")
            if isinstance(period_start, str) and period_start:
                return period_start
        money_weighted_return = self._as_dict(period_payload.get("money_weighted_return"))
        start_date = money_weighted_return.get("start_date")
        return start_date if isinstance(start_date, str) and start_date else None

    def _workspace_period_end(self, period_payload: dict[str, object]) -> str | None:
        daily_breakdowns = self._workspace_daily_breakdowns(period_payload)
        if daily_breakdowns:
            period_end = self._as_dict(daily_breakdowns[-1]).get("period_end")
            if isinstance(period_end, str) and period_end:
                return period_end
        money_weighted_return = self._as_dict(period_payload.get("money_weighted_return"))
        end_date = money_weighted_return.get("end_date")
        return end_date if isinstance(end_date, str) and end_date else None

    def _workspace_daily_breakdowns(self, period_payload: dict[str, object]) -> list[object]:
        portfolio_twr = self._as_dict(period_payload.get("portfolio_twr"))
        net_block = self._as_dict(portfolio_twr.get("net"))
        breakdowns = self._as_dict(net_block.get("breakdowns"))
        daily = breakdowns.get("daily")
        return daily if isinstance(daily, list) else []

    def _workspace_portfolio_open_date(self, payload: dict[str, object]) -> str | None:
        results_by_period = self._as_dict(payload.get("results_by_period"))
        for period_name in ("SI", "THREE_YEAR", "YTD", "QTD", "MTD"):
            period_payload = self._as_dict(results_by_period.get(period_name))
            period_start = self._workspace_period_start(period_payload)
            if period_start:
                return period_start
        return None

    def _return_base(self, summary_payload: dict[str, object], key: str) -> float | None:
        return_value = self._as_dict(summary_payload.get(key)).get("base")
        if isinstance(return_value, (int, float)):
            return float(return_value)
        if isinstance(return_value, str):
            try:
                return float(return_value)
            except ValueError:
                return None
        return None

    def _requested_sections(
        self,
        request_payload: dict[str, object],
        default_sections: list[str],
    ) -> set[str]:
        raw_sections = request_payload.get("sections")
        if not isinstance(raw_sections, list):
            return set(default_sections)
        sections: set[str] = set()
        for item in raw_sections:
            if isinstance(item, str):
                sections.add(item.upper())
        return sections or set(default_sections)

    def _required_string(self, payload: dict[str, object], *keys: str) -> str:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Missing required request field: {keys[0]}",
        )

    def _request_as_of_date(self, payload: dict[str, object]) -> str:
        return self._required_string(payload, *AS_OF_DATE_KEYS)

    def _copy_optional_request_string(
        self,
        target: dict[str, object],
        payload: dict[str, object],
        *,
        source_keys: tuple[str, ...],
        target_key: str,
    ) -> None:
        value = self._optional_string(payload, *source_keys)
        if value:
            target[target_key] = value

    def _optional_value(self, payload: dict[str, object], *keys: str) -> object | None:
        for key in keys:
            value = payload.get(key)
            if value is not None:
                return value
        return None

    def _optional_string(self, payload: dict[str, object], *keys: str) -> str | None:
        value = self._optional_value(payload, *keys)
        if isinstance(value, str) and value:
            return value
        return None

    @staticmethod
    def _as_dict(value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return value
        return {}

    @staticmethod
    def _as_list(value: object) -> list[object]:
        if isinstance(value, list):
            return value
        return []

    @staticmethod
    def _to_float(value: object) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return 0.0
        return 0.0

    @staticmethod
    def _to_int(value: object) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _safe_str(value: object) -> str:
        if isinstance(value, str):
            return value
        return ""
