from fastapi import HTTPException, status

from app.clients.pa_client import PaClient
from app.clients.pas_client import PasClient
from app.clients.risk_client import RiskClient
from app.config import settings


class ReportingReadService:
    def __init__(
        self,
        pas_client: PasClient | None = None,
        pa_client: PaClient | None = None,
        risk_client: RiskClient | None = None,
    ):
        self._pas_client = pas_client or PasClient(
            base_url=settings.pas_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        )
        self._pa_client = pa_client or PaClient(
            base_url=settings.pa_base_url,
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
        as_of_date = self._required_string(request_payload, "as_of_date", "asOfDate")
        requested_sections = self._requested_sections(
            request_payload=request_payload,
            default_sections=["WEALTH", "ALLOCATION", "PNL", "INCOME", "ACTIVITY"],
        )

        summary_request: dict[str, object] = {"as_of_date": as_of_date}
        reporting_currency = request_payload.get("reporting_currency") or request_payload.get(
            "reportingCurrency"
        )
        if isinstance(reporting_currency, str) and reporting_currency:
            summary_request["reporting_currency"] = reporting_currency

        status_code, payload = await self._pas_client.get_portfolio_summary(
            portfolio_id=portfolio_id,
            payload=summary_request,
            correlation_id=correlation_id,
        )
        summary = self._unwrap_pas_summary(status_code=status_code, payload=payload)

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
            allocation_status, allocation_payload = await self._pas_client.get_asset_allocation(
                portfolio_id=portfolio_id,
                payload=allocation_request,
                correlation_id=correlation_id,
            )
            allocation_response = self._unwrap_pas_allocation(
                status_code=allocation_status,
                payload=allocation_payload,
            )
            response["allocation"] = self._map_allocation_views(
                self._as_list(allocation_response.get("views"))
            )

        snapshot_sections: list[str] = []
        if "PNL" in requested_sections:
            snapshot_sections.append("OVERVIEW")

        if "INCOME" in requested_sections:
            income_request = self._build_reporting_window_request(request_payload)
            income_status, income_payload = await self._pas_client.get_income_summary(
                portfolio_id=portfolio_id,
                payload=income_request,
                correlation_id=correlation_id,
            )
            income_response = self._unwrap_pas_portfolio_reporting_summary(
                status_code=income_status,
                payload=income_payload,
                summary_name="income summary",
            )
            response["incomeSummary"] = self._map_income_summary(income_response)

        if "ACTIVITY" in requested_sections:
            activity_request = self._build_reporting_window_request(request_payload)
            activity_status, activity_payload = await self._pas_client.get_activity_summary(
                portfolio_id=portfolio_id,
                payload=activity_request,
                correlation_id=correlation_id,
            )
            activity_response = self._unwrap_pas_activity_summary(
                status_code=activity_status,
                payload=activity_payload,
            )
            response["activitySummary"] = self._map_activity_summary(activity_response)

        if snapshot_sections:
            snapshot_status, snapshot_payload = await self._pas_client.get_core_snapshot(
                portfolio_id=portfolio_id,
                as_of_date=as_of_date,
                include_sections=snapshot_sections,
            )
            snapshot = self._unwrap_pas_snapshot(
                status_code=snapshot_status, payload=snapshot_payload
            )
            overview = self._as_dict(snapshot.get("overview"))

            if "PNL" in requested_sections and "pnl_summary" in overview:
                response["pnlSummary"] = overview.get("pnl_summary")
        return response

    async def get_portfolio_review(
        self,
        portfolio_id: str,
        request_payload: dict[str, object],
        correlation_id: str | None,
    ) -> dict[str, object]:
        as_of_date = self._required_string(request_payload, "as_of_date", "asOfDate")
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

        status_code, payload = await self._pas_client.get_core_snapshot(
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
            include_sections=[
                "OVERVIEW",
                "ALLOCATION",
                "INCOME_AND_ACTIVITY",
                "HOLDINGS",
                "TRANSACTIONS",
            ],
        )
        snapshot = self._unwrap_pas_snapshot(status_code=status_code, payload=payload)
        response: dict[str, object] = {"portfolio_id": portfolio_id, "as_of_date": as_of_date}

        if "OVERVIEW" in requested_sections:
            response["overview"] = snapshot.get("overview")
        if "ALLOCATION" in requested_sections:
            response["allocation"] = snapshot.get("allocation")
        if "INCOME_AND_ACTIVITY" in requested_sections:
            response["incomeAndActivity"] = snapshot.get("incomeAndActivity")
        if "HOLDINGS" in requested_sections:
            response["holdings"] = snapshot.get("holdings")
        if "TRANSACTIONS" in requested_sections:
            response["transactions"] = snapshot.get("transactions")

        if "PERFORMANCE" in requested_sections:
            pa_status, pa_payload = await self._pa_client.get_pas_input_twr(
                portfolio_id=portfolio_id,
                as_of_date=as_of_date,
                periods=["MTD", "QTD", "YTD", "THREE_YEAR", "SI"],
            )
            if pa_status < status.HTTP_400_BAD_REQUEST:
                response["performance"] = self._map_pa_performance(pa_payload)
            else:
                response["performance"] = None

        if "RISK_ANALYTICS" in requested_sections:
            response["riskAnalytics"] = await self._build_risk_analytics(
                portfolio_id=portfolio_id,
                as_of_date=as_of_date,
            )

        return response

    async def _build_risk_analytics(
        self,
        portfolio_id: str,
        as_of_date: str,
    ) -> dict[str, object] | None:
        perf_status, perf_payload = await self._pas_client.get_performance_input(
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
            lookback_days=1200,
        )
        if perf_status >= status.HTTP_400_BAD_REQUEST:
            return None

        valuation_points = perf_payload.get("valuationPoints")
        performance_start_date = perf_payload.get("performanceStartDate")
        if not isinstance(valuation_points, list) or not valuation_points:
            return None
        if not isinstance(performance_start_date, str):
            return None

        twr_payload = {
            "portfolio_id": portfolio_id,
            "performance_start_date": performance_start_date,
            "metric_basis": "NET",
            "report_start_date": performance_start_date,
            "report_end_date": as_of_date,
            "analyses": [{"period": "EXPLICIT", "frequencies": ["daily"]}],
            "valuation_points": valuation_points,
            "currency": perf_payload.get("baseCurrency", "USD"),
            "output": {"include_cumulative": True, "include_timeseries": True},
        }
        twr_status, twr_response = await self._pa_client.calculate_twr(twr_payload)
        if twr_status >= status.HTTP_400_BAD_REQUEST:
            return None

        returns = self._extract_daily_returns_from_twr(twr_response)
        if not returns:
            return None

        risk_payload = {
            "scope": {"asOfDate": as_of_date, "netOrGross": "NET"},
            "periods": [{"type": "YTD"}, {"type": "THREE_YEAR"}],
            "metrics": ["VOLATILITY", "SHARPE", "DRAWDOWN", "VAR"],
            "portfolioOpenDate": performance_start_date,
            "returns": returns,
            "benchmarkReturns": [],
        }
        risk_status, risk_response = await self._risk_client.calculate_risk(risk_payload)
        if risk_status >= status.HTTP_400_BAD_REQUEST:
            return None

        results = self._as_dict(risk_response.get("results"))
        return {"results": results}

    def _extract_daily_returns_from_twr(
        self,
        twr_payload: dict[str, object],
    ) -> list[dict[str, object]]:
        results_by_period = self._as_dict(twr_payload.get("results_by_period"))
        period_payload = next(iter(results_by_period.values()), None)
        if not isinstance(period_payload, dict):
            return []

        breakdowns = self._as_dict(period_payload.get("breakdowns"))
        daily_items = breakdowns.get("daily")
        if not isinstance(daily_items, list):
            return []

        returns: list[dict[str, object]] = []
        for item in daily_items:
            if not isinstance(item, dict):
                continue
            period = item.get("period")
            summary = self._as_dict(item.get("summary"))
            value = summary.get("period_return_pct")
            if not isinstance(period, str) or not isinstance(value, (int, float)):
                continue
            returns.append({"date": period[:10], "value": float(value)})
        return returns

    def _unwrap_pas_snapshot(
        self, status_code: int, payload: dict[str, object]
    ) -> dict[str, object]:
        if status_code < status.HTTP_400_BAD_REQUEST:
            snapshot = self._as_dict(payload.get("snapshot"))
            if snapshot:
                return snapshot
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="lotus-core core snapshot payload missing snapshot section.",
            )
        if status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=payload.get("detail"))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"lotus-core core snapshot upstream failure: {payload}",
        )

    def _unwrap_pas_summary(
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

    def _unwrap_pas_allocation(
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
        if status_code == status.HTTP_422_UNPROCESSABLE_ENTITY:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=payload.get("detail")
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"lotus-core asset allocation upstream failure: {payload}",
        )

    def _unwrap_pas_portfolio_reporting_summary(
        self,
        *,
        status_code: int,
        payload: dict[str, object],
        summary_name: str,
    ) -> dict[str, object]:
        if status_code < status.HTTP_400_BAD_REQUEST:
            if isinstance(payload, dict) and "portfolios" in payload and "totals" in payload:
                return payload
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"lotus-core {summary_name} payload missing required fields.",
            )
        if status_code == status.HTTP_404_NOT_FOUND:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=payload.get("detail"))
        if status_code == status.HTTP_422_UNPROCESSABLE_ENTITY:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=payload.get("detail"),
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"lotus-core {summary_name} upstream failure: {payload}",
        )

    def _unwrap_pas_activity_summary(
        self, status_code: int, payload: dict[str, object]
    ) -> dict[str, object]:
        return self._unwrap_pas_portfolio_reporting_summary(
            status_code=status_code,
            payload=payload,
            summary_name="activity summary",
        )

    def _build_allocation_request(self, request_payload: dict[str, object]) -> dict[str, object]:
        dimensions = self._allocation_dimensions(request_payload)
        allocation_request: dict[str, object] = {"dimensions": dimensions}
        as_of_date = request_payload.get("as_of_date") or request_payload.get("asOfDate")
        if isinstance(as_of_date, str) and as_of_date:
            allocation_request["as_of_date"] = as_of_date
        reporting_currency = request_payload.get("reporting_currency") or request_payload.get(
            "reportingCurrency"
        )
        if isinstance(reporting_currency, str) and reporting_currency:
            allocation_request["reporting_currency"] = reporting_currency
        look_through_mode = request_payload.get("look_through_mode") or request_payload.get(
            "lookThroughMode"
        )
        if isinstance(look_through_mode, str) and look_through_mode:
            allocation_request["look_through_mode"] = look_through_mode
        return allocation_request

    def _build_reporting_window_request(
        self, request_payload: dict[str, object]
    ) -> dict[str, object]:
        end_date = self._required_string(request_payload, "as_of_date", "asOfDate")
        window_request: dict[str, object] = {
            "window": {
                "start_date": f"{end_date[:4]}-01-01",
                "end_date": end_date,
            }
        }
        reporting_currency = request_payload.get("reporting_currency") or request_payload.get(
            "reportingCurrency"
        )
        if isinstance(reporting_currency, str) and reporting_currency:
            window_request["reporting_currency"] = reporting_currency
        return window_request

    def _allocation_dimensions(self, request_payload: dict[str, object]) -> list[str]:
        raw_dimensions = request_payload.get("allocation_dimensions")
        if raw_dimensions is None:
            raw_dimensions = request_payload.get("allocationDimensions")
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

    def _map_income_summary(self, payload: dict[str, object]) -> dict[str, object]:
        portfolios = self._as_list(payload.get("portfolios"))
        portfolio_payload = self._as_dict(portfolios[0]) if portfolios else {}
        return self._as_dict(
            portfolio_payload.get("year_to_date")
            if portfolio_payload.get("year_to_date") is not None
            else self._as_dict(payload.get("totals")).get("year_to_date")
        )

    def _map_activity_summary(self, payload: dict[str, object]) -> dict[str, object]:
        totals = self._as_dict(payload.get("totals"))
        buckets = self._as_list(totals.get("buckets"))
        summary: dict[str, object] = {}
        for item in buckets:
            bucket_payload = self._as_dict(item)
            bucket = str(bucket_payload.get("bucket") or "").strip().lower()
            if not bucket:
                continue
            year_to_date = self._as_dict(bucket_payload.get("year_to_date"))
            summary[f"total_{bucket.lower()}"] = self._to_float(
                year_to_date.get("amount_reporting_currency")
            )
            summary[f"{bucket.lower()}_transaction_count"] = self._to_int(
                year_to_date.get("transaction_count")
            )
        return summary

    def _map_pa_performance(self, payload: dict[str, object]) -> dict[str, object]:
        results_by_period = self._as_dict(payload.get("resultsByPeriod"))
        summary: dict[str, object] = {}
        for period, row in results_by_period.items():
            row_dict = self._as_dict(row)
            summary[period] = {
                "start_date": row_dict.get("start_date"),
                "end_date": row_dict.get("end_date"),
                "net_cumulative_return": row_dict.get("net_cumulative_return"),
                "net_annualized_return": row_dict.get("net_annualized_return"),
                "gross_cumulative_return": row_dict.get("gross_cumulative_return"),
                "gross_annualized_return": row_dict.get("gross_annualized_return"),
            }
        return {"summary": summary}

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
