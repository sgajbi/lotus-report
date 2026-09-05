from dataclasses import dataclass
from datetime import datetime, timezone

from app.application_errors import (
    ReportingNotFoundError,
    ReportingUpstreamError,
    ReportingValidationError,
)
from app.clients.core_query_client import CoreQueryClient
from app.clients.performance_client import PerformanceClient
from app.clients.risk_client import RiskClient
from app.config import settings
from app.report_ordering_catalogue.definitions import PORTFOLIO_REVIEW_SECTION_DEFINITIONS
from app.services.attribution_capture import capture_attribution
from app.services.performance_contribution import (
    map_contribution_levels,
    map_position_contributions,
    security_id_from_position_id,
)
from app.services.portfolio_review_advisor import build_advisor_sections
from app.services.review_evidence import build_review_evidence
from app.services.risk_supportability import (
    BENCHMARK_RISK_METRICS,
    risk_supportability,
)
from app.services.transaction_evidence import (
    merge_transaction_source_product,
    transaction_window_supportability,
)

HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE_ENTITY = 422

AS_OF_DATE_KEYS = ("as_of_date",)
REPORTING_CURRENCY_KEYS = ("reporting_currency",)
LOOK_THROUGH_MODE_KEYS = ("look_through_mode",)
ALLOCATION_DIMENSIONS_KEYS = ("allocation_dimensions",)
BENCHMARK_CODE_KEYS = ("benchmark_code",)
BENCHMARK_CODE_ALIASES = {
    "BMK_GLOBAL_BALANCED_60_40": "BMK_PB_GLOBAL_BALANCED_60_40",
}
CLIENT_ID_KEYS = ("client_id",)
#: The rolling-trend window (observations): one quarter of daily
#: observations, stated with the series on the page - a trend without
#: its window is not interpretable.
ROLLING_TREND_WINDOW_OBSERVATIONS = 63
RISK_METRICS = ("VOLATILITY", "SHARPE", "DRAWDOWN", "VAR")
PERFORMANCE_REVIEW_PERIODS: tuple[dict[str, object], ...] = (
    {"period": "1M", "frequencies": ["daily"]},
    {"period": "3M", "frequencies": ["daily"]},
    {"period": "YTD", "frequencies": ["daily", "monthly"]},
    {"period": "1Y", "frequencies": ["daily", "monthly"]},
    {"period": "5Y", "frequencies": ["daily", "yearly"]},
    {"period": "SI", "frequencies": ["daily", "yearly"]},
)


@dataclass(frozen=True)
class _TransactionRowsResult:
    rows: list[dict[str, object]]
    source_total: int | None
    fetched_pages: int
    supportability: dict[str, object]
    source_product: dict[str, object]


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
        transaction_result: _TransactionRowsResult | None = None
        transaction_rows: list[dict[str, object]] | None = None
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
            transaction_result = await self._list_transaction_rows_result(
                portfolio_id=portfolio_id,
                correlation_id=correlation_id,
                params=self._build_transaction_window_params(request_payload),
            )
            transaction_rows = transaction_result.rows
            if "INCOME" in requested_sections:
                response["incomeSummary"] = self._map_income_summary_from_rows(transaction_rows)
            if "ACTIVITY" in requested_sections:
                response["activitySummary"] = self._map_activity_summary_from_rows(transaction_rows)
            response["transactionWindowSupportability"] = transaction_result.supportability

        if "PNL" in requested_sections:
            (
                positions_status,
                positions_payload,
            ) = await self._core_query_client.get_portfolio_positions(
                portfolio_id=portfolio_id,
                params=self._build_position_params(request_payload),
                correlation_id=correlation_id,
            )
            holdings = self._unwrap_core_query_positions(
                status_code=positions_status,
                payload=positions_payload,
            )
            if transaction_rows is None:
                transaction_result = await self._list_transaction_rows_result(
                    portfolio_id=portfolio_id,
                    correlation_id=correlation_id,
                    params=self._build_transaction_window_params(request_payload),
                )
                transaction_rows = transaction_result.rows
            response["pnlSummary"] = self._map_pnl_summary(
                summary=summary,
                holdings=holdings,
                transaction_rows=transaction_rows,
                transaction_result=transaction_result,
            )
        return response

    async def get_portfolio_review(
        self,
        portfolio_id: str,
        request_payload: dict[str, object],
        correlation_id: str | None,
        admitted_tenant_id: str | None = None,
        evidence_posture: str = "ephemeral_composition",
    ) -> dict[str, object]:
        as_of_date = self._request_as_of_date(request_payload)
        requested_sections = self._requested_sections(
            request_payload=request_payload,
            default_sections=[
                "CLIENT_PROFILE",
                "OVERVIEW",
                "ALLOCATION",
                "PERFORMANCE",
                "RISK_ANALYTICS",
                "INCOME_AND_ACTIVITY",
                "HOLDINGS",
                "TRANSACTIONS",
            ],
        )
        requested_sections.add("CLIENT_PROFILE")

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
        response["reviewPeriod"] = self._review_period(as_of_date)
        response["reportingCurrency"] = self._reporting_currency(request_payload, summary)
        response["methodology"] = self._review_methodology(request_payload)
        response["clientProfile"] = await self._portfolio_client_profile(
            portfolio_id=portfolio_id,
            correlation_id=correlation_id,
        )
        transaction_result: _TransactionRowsResult | None = None
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
                transaction_result = await self._list_transaction_rows_result(
                    portfolio_id=portfolio_id,
                    correlation_id=correlation_id,
                    params=self._build_transaction_window_params(request_payload),
                )
                transaction_rows = transaction_result.rows
            response["incomeAndActivity"] = {
                "incomeSummary": self._map_income_summary_from_rows(transaction_rows),
                "activitySummary": self._map_activity_summary_from_rows(transaction_rows),
                "realizedPnlSummary": self._summarize_realized_pnl_rows(transaction_rows),
                "supportability": self._transaction_rows_supportability(transaction_result),
                "sourceProduct": self._transaction_source_product(transaction_result),
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
                transaction_result = await self._list_transaction_rows_result(
                    portfolio_id=portfolio_id,
                    correlation_id=correlation_id,
                    params=self._build_transaction_window_params(request_payload),
                )
                transaction_rows = transaction_result.rows
            response["transactions"] = self._map_review_transactions(
                transaction_rows,
                transaction_result=transaction_result,
            )

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
                    periods=PERFORMANCE_REVIEW_PERIODS,
                )
            )
            if self._workspace_summary_ready(performance_status, performance_payload):
                workspace_summary_payload = performance_payload
            if self._workspace_summary_ready(performance_status, performance_payload):
                performance = self._map_workspace_performance(
                    performance_payload,
                    request_payload=request_payload,
                )
                (
                    contribution_status,
                    contribution_payload,
                ) = await self._performance_client.get_contribution(
                    self._build_contribution_request(
                        portfolio_id=portfolio_id,
                        as_of_date=as_of_date,
                        request_payload=request_payload,
                    )
                )
                contribution = self._map_performance_contribution(
                    status_code=contribution_status,
                    payload=contribution_payload,
                )
                performance["contribution"] = contribution
                response["performance"] = performance
                if "HOLDINGS" in requested_sections:
                    self._enrich_holdings_with_contribution(
                        response=response,
                        contribution=contribution,
                    )
            else:
                response["performance"] = None

        if "PERFORMANCE_ATTRIBUTION" in requested_sections:
            response["attribution"] = await capture_attribution(
                performance_client=self._performance_client,
                portfolio_id=portfolio_id,
                as_of_date=as_of_date,
                # None = portfolio's assigned benchmark; an omission, never "".
                benchmark_code=self._normalized_benchmark_code(
                    self._optional_string(request_payload, *BENCHMARK_CODE_KEYS)
                ),
            )

        if "RISK_ANALYTICS" in requested_sections:
            response["riskAnalytics"] = await self._build_risk_analytics(
                portfolio_id=portfolio_id,
                as_of_date=as_of_date,
                request_payload=request_payload,
                workspace_summary_payload=workspace_summary_payload,
            )
            # The rolling-risk trend rides the SAME ordered section (#255):
            # the risk page gains "is risk changing?" beside its point-in-time
            # numbers, from source-owned series.
            response["riskTrend"] = await self._build_risk_trend(
                portfolio_id=portfolio_id,
                as_of_date=as_of_date,
                request_payload=request_payload,
            )
        if "RISK_ATTRIBUTION" in requested_sections:
            # Ordered explicitly, never by default (#254's evidence gate):
            # the risk page's "what risk did we take for the result".
            response["riskAttribution"] = await self._build_risk_attribution(
                portfolio_id=portfolio_id,
                as_of_date=as_of_date,
                request_payload=request_payload,
            )

        client_sections = self._build_client_sections(
            response=response,
            requested_sections=requested_sections,
        )
        response["readiness"] = self._review_readiness(client_sections=client_sections)
        response["keyFigures"] = self._review_key_figures(response)
        response["reviewObservations"] = self._review_observations(response)
        response["reportCoverage"] = self._review_report_coverage(response)
        response["reportStructure"] = self._report_structure(response, client_sections)
        response["advisorBriefing"] = self._advisor_briefing(response)
        response["aiReadiness"] = self._ai_readiness(response)
        response["evidence"] = build_review_evidence(
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
            correlation_id=correlation_id,
            response=response,
            admitted_tenant_id=admitted_tenant_id,
            evidence_posture=evidence_posture,
        )
        response["client_sections"] = client_sections
        response["upstreamCapabilityAudit"] = self._upstream_capability_audit(response)
        response["audience"] = self._review_audience(client_sections)
        response["disclosures"] = self._review_disclosures(response)
        response["advisor_sections"] = build_advisor_sections(
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
            response=response,
            client_sections=client_sections,
        )
        return response

    def _new_review_response(self, *, portfolio_id: str, as_of_date: str) -> dict[str, object]:
        return {
            "contract_version": settings.contract_version,
            "report_id": f"portfolio-review:{portfolio_id}:{as_of_date}",
            "portfolio_id": portfolio_id,
            "as_of_date": as_of_date,
            "generated_at": datetime.now(timezone.utc),
            "reviewPeriod": self._review_period(as_of_date),
            "reportingCurrency": None,
            "audience": {},
            "readiness": {"status": "ready"},
            "methodology": self._review_methodology({}),
            "evidence": {},
            "keyFigures": {},
            "reportCoverage": {},
            "upstreamCapabilityAudit": {},
            "reviewObservations": [],
            "clientProfile": {},
            "reportStructure": {},
            "advisorBriefing": {},
            "aiReadiness": {},
            "disclosures": [],
        }

    async def _portfolio_client_profile(
        self,
        *,
        portfolio_id: str,
        correlation_id: str | None,
    ) -> dict[str, object]:
        get_detail = getattr(self._core_query_client, "get_portfolio_detail", None)
        if get_detail is None:
            return self._client_profile_unavailable(
                portfolio_id=portfolio_id,
                reason_code="source_client_does_not_support_portfolio_detail",
            )
        status_code, payload = await get_detail(
            portfolio_id=portfolio_id,
            correlation_id=correlation_id,
        )
        if status_code >= HTTP_BAD_REQUEST:
            return self._client_profile_unavailable(
                portfolio_id=portfolio_id,
                reason_code="source_unavailable",
            )
        profile_payload = self._as_dict(payload)
        if not profile_payload:
            return self._client_profile_unavailable(
                portfolio_id=portfolio_id,
                reason_code="source_payload_missing",
            )
        required_fields = (
            "client_id",
            "advisor_id",
            "booking_center_code",
            "portfolio_type",
            "objective",
            "risk_exposure",
            "investment_time_horizon",
        )
        missing_fields = [
            field
            for field in required_fields
            if not self._profile_field_present(profile_payload, field)
        ]
        return {
            "status": "partial" if missing_fields else "present",
            "identity": {
                "client_id": self._safe_str(profile_payload.get("client_id")),
                "advisor_id": self._safe_str(profile_payload.get("advisor_id")),
                "booking_center_code": self._safe_str(profile_payload.get("booking_center_code")),
            },
            "portfolio_profile": {
                "portfolio_id": self._safe_str(profile_payload.get("portfolio_id")) or portfolio_id,
                "base_currency": self._safe_str(profile_payload.get("base_currency")),
                "open_date": self._safe_str(profile_payload.get("open_date")),
                "status": self._safe_str(profile_payload.get("status")),
            },
            "mandate_profile": {
                "portfolio_type": self._safe_str(profile_payload.get("portfolio_type")),
                "objective": self._safe_str(profile_payload.get("objective")),
                "risk_exposure": self._safe_str(profile_payload.get("risk_exposure")),
                "investment_time_horizon": self._safe_str(
                    profile_payload.get("investment_time_horizon")
                ),
                "is_leverage_allowed": bool(profile_payload.get("is_leverage_allowed")),
                "cost_basis_method": self._safe_str(profile_payload.get("cost_basis_method")),
            },
            "missing_fields": missing_fields,
            "source": {
                "service": "lotus-core",
                "endpoint": f"/portfolios/{portfolio_id}",
            },
        }

    def _client_profile_unavailable(
        self,
        *,
        portfolio_id: str,
        reason_code: str,
    ) -> dict[str, object]:
        return {
            "status": "unavailable",
            "reason_code": reason_code,
            "identity": {},
            "portfolio_profile": {"portfolio_id": portfolio_id},
            "mandate_profile": {},
            "missing_fields": [
                "client_id",
                "advisor_id",
                "booking_center_code",
                "portfolio_type",
                "objective",
                "risk_exposure",
                "investment_time_horizon",
            ],
            "source": {
                "service": "lotus-core",
                "endpoint": f"/portfolios/{portfolio_id}",
            },
        }

    def _profile_field_present(self, payload: dict[str, object], field: str) -> bool:
        value = payload.get(field)
        if isinstance(value, bool):
            return True
        return value not in (None, "", {}, [])

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
        partial_sections = [
            self._safe_str(section.get("title"))
            for section in client_sections
            if section.get("status") == "partial"
        ]

        if not unavailable_sections and not partial_sections:
            return {"status": "ready"}
        if unavailable_sections:
            return {
                "status": "partial",
                "reason": (
                    "Unavailable sections for the selected request: "
                    + ", ".join(unavailable_sections)
                ),
            }
        return {
            "status": "partial",
            "reason": ("Partial sections for the selected request: " + ", ".join(partial_sections)),
        }

    def _build_client_sections(
        self,
        *,
        response: dict[str, object],
        requested_sections: set[str],
    ) -> list[dict[str, object]]:
        return [
            self._build_review_section(
                requested_key=definition.section_id,
                section_id=definition.response_section_id,
                title=definition.response_title,
                response_key=definition.response_key,
                response=response,
                requested_sections=requested_sections,
            )
            for definition in PORTFOLIO_REVIEW_SECTION_DEFINITIONS
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
        if supportability_status in {"partial", "unavailable", "pending"}:
            return {
                "section_id": section_id,
                "title": title,
                "status": supportability_status,
                "reason_code": self._supportability_reason_code(supportability),
                "message": self._supportability_message(supportability, title),
                "items": self._section_items(section_id, section_payload),
            }

        if self._section_not_applicable(section_id=section_id, section_payload=section_payload):
            return {
                "section_id": section_id,
                "title": title,
                "status": "not_applicable",
                "reason_code": "no_applicable_activity",
                "message": f"{title} has no applicable activity for this request.",
                "items": self._section_items(section_id, section_payload),
            }

        return {
            "section_id": section_id,
            "title": title,
            "status": "ready",
            "items": self._section_items(section_id, section_payload),
        }

    def _section_not_applicable(self, *, section_id: str, section_payload: object) -> bool:
        section = self._as_dict(section_payload)
        if section_id == "holdings_appendix":
            return self._to_int(section.get("positionCount")) == 0
        if section_id == "transactions_appendix":
            return self._to_int(section.get("transactionCount")) == 0
        if section_id == "income_cash_activity":
            income_summary = self._as_dict(section.get("incomeSummary"))
            activity_summary = self._as_dict(section.get("activitySummary"))
            income_count = self._to_int(income_summary.get("transaction_count"))
            activity_count = sum(
                self._to_int(value)
                for key, value in activity_summary.items()
                if key.endswith("_transaction_count")
            )
            return income_count + activity_count == 0
        return False

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
                    periods=["YTD", "5Y", "SI"],
                )
            )
            if summary_status >= HTTP_BAD_REQUEST:
                return self._risk_unavailable(
                    reason_code="risk_return_history_unavailable",
                    message=(
                        "Risk Review is unavailable because performance return history could "
                        "not be sourced."
                    ),
                    request_payload=request_payload,
                )

        returns = self._extract_daily_returns_from_workspace_summary(workspace_summary_payload)
        if not returns:
            return self._risk_unavailable(
                reason_code="missing_return_history",
                message=(
                    "Risk Review is unavailable because no daily return history was available "
                    "for the selected request."
                ),
                request_payload=request_payload,
            )
        portfolio_open_date = self._workspace_portfolio_open_date(workspace_summary_payload)
        if portfolio_open_date is None:
            return self._risk_unavailable(
                reason_code="missing_return_history",
                message=(
                    "Risk Review is unavailable because the sourced return history did not "
                    "include a portfolio open date."
                ),
                request_payload=request_payload,
            )
        risk_payload: dict[str, object] = {
            "input_mode": "stateful",
            "stateful_input": self._build_risk_stateful_input(
                portfolio_id=portfolio_id,
                as_of_date=as_of_date,
                request_payload=request_payload,
            ),
        }
        risk_status, risk_response = await self._risk_client.calculate_risk(risk_payload)
        period_failures: list[dict[str, object]] = []
        if risk_status >= HTTP_BAD_REQUEST:
            risk_response, period_failures = await self._calculate_risk_by_period(
                risk_payload,
                fallback_reason_code="risk_period_upstream_failure",
            )
            if not self._as_dict(risk_response.get("results")):
                return self._risk_unavailable(
                    reason_code="risk_upstream_failure",
                    message=(
                        "Risk Review is unavailable because lotus-risk could not calculate metrics."
                    ),
                    request_payload=request_payload,
                )

        results = self._as_dict(risk_response.get("results"))
        metadata = self._as_dict(risk_response.get("metadata"))
        supportability = risk_supportability(
            results=results,
            metadata=metadata,
            benchmark_code=self._optional_string(request_payload, *BENCHMARK_CODE_KEYS),
            period_failures=period_failures,
        )
        return {
            "source": {
                "service": "lotus-risk",
                "endpoint": "/analytics/risk/calculate",
            },
            "methodology": {
                "metrics": self._requested_risk_metrics(request_payload),
                "return_source": (
                    "lotus-risk stateful sourcing via lotus-performance returns-series"
                ),
                "return_basis": "NET",
                "benchmark_code": self._optional_string(request_payload, *BENCHMARK_CODE_KEYS),
            },
            "supportability": supportability,
            "summary": self._risk_metric_summary(results),
            "results": results,
            "metadata": metadata,
        }

    async def _calculate_risk_by_period(
        self,
        risk_payload: dict[str, object],
        *,
        fallback_reason_code: str,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        stateful_input = self._as_dict(risk_payload.get("stateful_input"))
        periods = self._as_list(stateful_input.get("periods"))
        if len(periods) <= 1:
            return {}, []

        merged_results: dict[str, object] = {}
        merged_metadata: dict[str, object] = {}
        period_failures: list[dict[str, object]] = []
        for period in periods:
            period_payload = self._as_dict(period)
            period_risk_payload = {
                "input_mode": risk_payload.get("input_mode"),
                "stateful_input": {
                    **stateful_input,
                    "periods": [period_payload],
                },
            }
            period_status, period_response = await self._risk_client.calculate_risk(
                period_risk_payload
            )
            period_name = self._safe_str(period_payload.get("name")) or self._safe_str(
                period_payload.get("type")
            )
            if period_status >= HTTP_BAD_REQUEST:
                period_failures.append(
                    {
                        "period": period_name,
                        "code": fallback_reason_code,
                        "status_code": period_status,
                        "message": self._upstream_error_message(period_response),
                    }
                )
                continue
            merged_results.update(self._as_dict(period_response.get("results")))
            if not merged_metadata:
                merged_metadata = self._as_dict(period_response.get("metadata"))

        if period_failures:
            merged_metadata["period_failures"] = period_failures
        return {"results": merged_results, "metadata": merged_metadata}, period_failures

    def _upstream_error_message(self, payload: dict[str, object]) -> str:
        error = self._as_dict(payload.get("error"))
        message = self._safe_str(error.get("message"))
        if message:
            return message
        detail = self._as_dict(payload.get("detail"))
        message = self._safe_str(detail.get("message"))
        if message:
            return message
        return "Upstream risk calculation failed for this period."

    def _risk_unavailable(
        self,
        *,
        reason_code: str,
        message: str,
        request_payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        request_payload = request_payload or {}
        return {
            "source": {
                "service": "lotus-risk",
                "endpoint": "/analytics/risk/calculate",
            },
            "methodology": {
                "metrics": self._requested_risk_metrics(request_payload),
                "return_basis": "NET",
            },
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
                "beta": self._metric_value(metrics, "BETA"),
                "tracking_error": self._metric_value(metrics, "TRACKING_ERROR"),
                "information_ratio": self._metric_value(metrics, "INFORMATION_RATIO"),
                "benchmark_relative_risk": self._metric_value(metrics, "TRACKING_ERROR"),
            }
        return summary

    def _metric_value(self, metrics: dict[str, object], metric_name: str) -> object | None:
        metric = self._as_dict(metrics.get(metric_name))
        return metric.get("value")

    def _supportability_reason_code(self, supportability: dict[str, object]) -> str:
        prioritized_notes = self._supportability_prioritized_notes(supportability)
        for note_payload in prioritized_notes:
            code = note_payload.get("code")
            if isinstance(code, str) and code:
                return code
        return "source_unavailable"

    def _supportability_message(self, supportability: dict[str, object], title: str) -> str:
        prioritized_notes = self._supportability_prioritized_notes(supportability)
        for note_payload in prioritized_notes:
            message = note_payload.get("message")
            if isinstance(message, str) and message:
                return message
        return f"{title} is unavailable for this request."

    def _supportability_prioritized_notes(
        self, supportability: dict[str, object]
    ) -> list[dict[str, object]]:
        notes = [self._as_dict(note) for note in self._as_list(supportability.get("notes"))]
        priority = {"blocking": 0, "warning": 1, "informational": 2}
        return sorted(
            notes,
            key=lambda note: priority.get(self._safe_str(note.get("severity")), 99),
        )

    def _extract_daily_returns_from_workspace_summary(
        self,
        workspace_payload: dict[str, object],
    ) -> list[dict[str, object]]:
        results_by_period = self._as_dict(workspace_payload.get("results_by_period"))
        daily_items: list[object] = []
        for period_name in ("5Y", "SI", "YTD", "3M", "1M"):
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
        if status_code < HTTP_BAD_REQUEST:
            if isinstance(payload, dict) and {"portfolio_id", "totals", "snapshot_metadata"} <= set(
                payload
            ):
                return payload
            raise ReportingUpstreamError(
                "lotus-core portfolio summary payload missing required fields."
            )
        if status_code == HTTP_NOT_FOUND:
            raise ReportingNotFoundError(payload.get("detail"))
        raise ReportingUpstreamError(f"lotus-core portfolio summary upstream failure: {payload}")

    def _unwrap_core_query_allocation(
        self, status_code: int, payload: dict[str, object]
    ) -> dict[str, object]:
        if status_code < HTTP_BAD_REQUEST:
            if isinstance(payload, dict) and "views" in payload:
                return payload
            raise ReportingUpstreamError(
                "lotus-core asset allocation payload missing required fields."
            )
        if status_code == HTTP_NOT_FOUND:
            raise ReportingNotFoundError(payload.get("detail"))
        if status_code == HTTP_UNPROCESSABLE_ENTITY:
            raise ReportingValidationError(payload.get("detail"))
        raise ReportingUpstreamError(f"lotus-core asset allocation upstream failure: {payload}")

    def _unwrap_core_query_positions(
        self, status_code: int, payload: dict[str, object]
    ) -> dict[str, object]:
        if status_code < HTTP_BAD_REQUEST:
            if isinstance(payload, dict) and "positions" in payload:
                return self._map_holdings_from_positions(payload)
            raise ReportingUpstreamError("lotus-core positions payload missing required fields.")
        if status_code == HTTP_NOT_FOUND:
            raise ReportingNotFoundError(payload.get("detail"))
        raise ReportingUpstreamError(f"lotus-core positions upstream failure: {payload}")

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

    def _map_pnl_summary(
        self,
        *,
        summary: dict[str, object],
        holdings: dict[str, object],
        transaction_rows: list[dict[str, object]],
        transaction_result: _TransactionRowsResult | None,
    ) -> dict[str, object]:
        totals = self._as_dict(summary.get("totals"))
        invested_market_value = self._to_float(
            totals.get("invested_market_value_reporting_currency")
        )
        holding_rows = self._holding_rows(holdings)
        unrealized_pnl_rows = [
            row
            for row in holding_rows
            if self._optional_number_raw(row.get("unrealized_pnl_reporting_currency")) is not None
        ]
        realized_pnl_rows = [
            row for row in transaction_rows if self._realized_pnl_reporting_amount(row) is not None
        ]
        unrealized_pnl = (
            sum(
                self._to_float(row.get("unrealized_pnl_reporting_currency"))
                for row in unrealized_pnl_rows
            )
            if unrealized_pnl_rows
            else None
        )
        realized_pnl = (
            sum(
                self._to_float(self._realized_pnl_reporting_amount(row))
                for row in realized_pnl_rows
            )
            if realized_pnl_rows
            else None
        )
        unrealized_status = self._coverage_status(len(unrealized_pnl_rows), len(holding_rows))
        realized_status = self._summary_realized_pnl_status(
            realized_count=len(realized_pnl_rows),
            transaction_count=len(transaction_rows),
            transaction_result=transaction_result,
        )
        sourced_components = [
            component for component in (unrealized_pnl, realized_pnl) if component is not None
        ]
        return {
            "invested_market_value_reporting_currency": invested_market_value,
            "unrealized_pnl_reporting_currency": unrealized_pnl,
            "unrealized_pnl_status": unrealized_status,
            "realized_pnl_reporting_currency": realized_pnl,
            "realized_pnl_status": realized_status,
            "total_pnl": sum(sourced_components) if sourced_components else None,
            "total_pnl_status": self._summary_total_pnl_status(
                unrealized_status=unrealized_status,
                realized_status=realized_status,
                sourced_component_count=len(sourced_components),
            ),
            "source_methodology": "sourced_position_unrealized_and_transaction_realized_pnl",
            "supportability": {
                "status": self._summary_pnl_supportability_status(
                    unrealized_status=unrealized_status,
                    realized_status=realized_status,
                    transaction_result=transaction_result,
                ),
                "notes": self._summary_pnl_supportability_notes(
                    unrealized_status=unrealized_status,
                    realized_status=realized_status,
                    transaction_result=transaction_result,
                ),
            },
        }

    def _summary_realized_pnl_status(
        self,
        *,
        realized_count: int,
        transaction_count: int,
        transaction_result: _TransactionRowsResult | None,
    ) -> str:
        supportability = self._transaction_rows_supportability(transaction_result)
        if supportability.get("status") == "partial":
            return "partial" if realized_count else "not_sourced"
        if realized_count:
            return "present"
        if transaction_count:
            return "not_applicable"
        return "not_applicable"

    @staticmethod
    def _summary_total_pnl_status(
        *,
        unrealized_status: str,
        realized_status: str,
        sourced_component_count: int,
    ) -> str:
        if sourced_component_count == 0:
            return "not_sourced"
        if unrealized_status == "present" and realized_status in {"present", "not_applicable"}:
            return "present"
        return "partial"

    def _summary_pnl_supportability_status(
        self,
        *,
        unrealized_status: str,
        realized_status: str,
        transaction_result: _TransactionRowsResult | None,
    ) -> str:
        transaction_supportability = self._transaction_rows_supportability(transaction_result)
        if transaction_supportability.get("status") == "partial":
            return "partial"
        if unrealized_status == "present" and realized_status in {"present", "not_applicable"}:
            return "ready"
        if unrealized_status == "not_sourced" and realized_status == "not_applicable":
            return "not_sourced"
        return "partial"

    def _summary_pnl_supportability_notes(
        self,
        *,
        unrealized_status: str,
        realized_status: str,
        transaction_result: _TransactionRowsResult | None,
    ) -> list[dict[str, object]]:
        notes: list[dict[str, object]] = []
        if unrealized_status != "present":
            notes.append(
                {
                    "code": "summary_unrealized_pnl_not_fully_sourced",
                    "severity": "warning",
                    "status": unrealized_status,
                    "message": (
                        "Summary unrealized P&L uses lotus-core position rows only when "
                        "position-level unrealized P&L is sourced."
                    ),
                }
            )
        if realized_status in {"not_sourced", "partial"}:
            notes.append(
                {
                    "code": "summary_realized_pnl_not_fully_sourced",
                    "severity": "warning",
                    "status": realized_status,
                    "message": (
                        "Summary realized P&L uses lotus-core transaction rows only when "
                        "transaction-level realized gain/loss is sourced within report-owned "
                        "query budgets."
                    ),
                }
            )
        for note in self._as_list(
            self._transaction_rows_supportability(transaction_result).get("notes")
        ):
            notes.append(self._as_dict(note))
        return notes

    def _map_holdings_from_positions(self, payload: dict[str, object]) -> dict[str, object]:
        holdings_by_asset_class: dict[str, list[dict[str, object]]] = {}
        source_product = self._holdings_source_product(payload)
        for item in self._as_list(payload.get("positions")):
            row = self._as_dict(item)
            asset_class = self._safe_str(row.get("asset_class")) or "UNKNOWN"
            holdings_by_asset_class.setdefault(asset_class, []).append(
                {
                    "position_id": self._safe_str(row.get("position_id")) or None,
                    "security_id": self._safe_str(row.get("security_id")),
                    "instrument_name": self._safe_str(
                        row.get("instrument_name") or row.get("description")
                    ),
                    "isin": self._safe_str(row.get("isin")) or None,
                    "quantity": self._to_float(row.get("quantity")),
                    "position_date": self._safe_str(row.get("position_date")) or None,
                    "product_type": self._safe_str(row.get("product_type")) or None,
                    "sector": self._safe_str(row.get("sector")) or None,
                    "country_of_risk": self._safe_str(row.get("country_of_risk")) or None,
                    "rating": self._safe_str(row.get("rating")) or None,
                    "liquidity_tier": self._safe_str(row.get("liquidity_tier")) or None,
                    "held_since_date": self._safe_str(row.get("held_since_date")) or None,
                    "maturity_date": self._safe_str(row.get("maturity_date")) or None,
                    "position_state_status": self._safe_str(
                        row.get("position_state_status") or row.get("position_status")
                    )
                    or None,
                    "position_state_epoch": self._safe_str(row.get("position_state_epoch")) or None,
                    "row_evidence_timestamp": self._safe_str(
                        row.get("latest_evidence_timestamp") or row.get("evidence_timestamp")
                    )
                    or None,
                    "row_snapshot_id": self._safe_str(row.get("snapshot_id")) or None,
                    "source_system": self._safe_str(row.get("source_system")) or None,
                    "source_record_id": self._safe_str(row.get("source_record_id")) or None,
                    "source_transaction_id": self._safe_str(row.get("source_transaction_id"))
                    or None,
                    "lot_status": self._safe_str(row.get("lot_status")) or None,
                    "market_price": self._position_number(
                        row, ("market_price",), ("market_price",)
                    ),
                    "cost_basis_reporting_currency": self._position_number(
                        row,
                        ("cost_basis_reporting_currency", "cost_basis"),
                    ),
                    "cost_basis_local": self._position_number(row, ("cost_basis_local",)),
                    "market_value_reporting_currency": self._position_market_value(row),
                    "market_value_local": self._position_number(
                        row,
                        ("market_value_local",),
                        ("market_value_local",),
                    ),
                    "unrealized_pnl_reporting_currency": self._position_number(
                        row,
                        (
                            "unrealized_pnl_reporting_currency",
                            "unrealized_gain_loss",
                            "unrealized_pnl",
                        ),
                        ("unrealized_gain_loss", "unrealized_pnl"),
                    ),
                    "unrealized_pnl_local": self._position_number(
                        row,
                        ("unrealized_pnl_local", "unrealized_gain_loss_local"),
                        ("unrealized_gain_loss_local", "unrealized_pnl_local"),
                    ),
                    "unrealized_pnl_pct": self._position_unrealized_pnl_pct(row),
                    "weight": self._to_float(row.get("weight")),
                    "currency": self._safe_str(row.get("currency")),
                }
            )
        return {
            "holdingsByAssetClass": holdings_by_asset_class,
            "positionCount": self._to_int(payload.get("total"))
            or sum(len(rows) for rows in holdings_by_asset_class.values()),
            "supportability": self._holdings_supportability(source_product),
            "sourceProduct": source_product,
        }

    def _holdings_source_product(self, payload: dict[str, object]) -> dict[str, object]:
        source_product: dict[str, object] = {}
        for source_key, target_key in (
            ("product_name", "product_name"),
            ("product_version", "product_version"),
            ("tenant_id", "tenant_id"),
            ("generated_at", "generated_at"),
            ("as_of_date", "as_of_date"),
            ("data_quality_status", "data_quality_status"),
            ("reconciliation_status", "reconciliation_status"),
            ("latest_evidence_timestamp", "latest_evidence_timestamp"),
            ("restatement_version", "restatement_version"),
            ("source_batch_fingerprint", "source_batch_fingerprint"),
            ("snapshot_id", "snapshot_id"),
            ("content_hash", "content_hash"),
            ("policy_version", "policy_version"),
            ("correlation_id", "correlation_id"),
            ("portfolio_id", "portfolio_id"),
            ("reporting_currency", "reporting_currency"),
            ("position_state_supportability", "position_state_supportability"),
            ("source_reported_cash_weight_supportability", "cash_weight_supportability"),
        ):
            if payload.get(source_key) is not None:
                source_product[target_key] = payload.get(source_key)
        source_product.setdefault("product_name", "HoldingsAsOf")
        source_product.setdefault("product_version", "v1")
        source_product["source_service"] = "lotus-core"
        source_product["source_endpoint"] = "/portfolios/{portfolio_id}/positions"
        source_product["source_total"] = self._to_int(payload.get("total"))
        reason_codes = self._as_list(payload.get("reason_codes"))
        if reason_codes:
            source_product["reason_codes"] = [
                self._safe_str(reason_code) for reason_code in reason_codes
            ]
        return source_product

    def _holdings_supportability(self, source_product: dict[str, object]) -> dict[str, object]:
        notes = self._holdings_source_product_supportability_notes(source_product)
        return {"status": "partial" if notes else "ready", "notes": notes}

    def _holdings_source_product_supportability_notes(
        self, source_product: dict[str, object]
    ) -> list[dict[str, object]]:
        notes: list[dict[str, object]] = []
        required_fields = (
            "product_name",
            "product_version",
            "tenant_id",
            "generated_at",
            "as_of_date",
            "data_quality_status",
            "reconciliation_status",
            "latest_evidence_timestamp",
            "restatement_version",
            "source_batch_fingerprint",
            "snapshot_id",
            "policy_version",
            "correlation_id",
        )
        missing_fields = [
            field for field in required_fields if source_product.get(field) in (None, "", [], {})
        ]
        if missing_fields:
            notes.append(
                {
                    "code": "holdings_as_of_trust_metadata_incomplete",
                    "severity": "warning",
                    "missing_fields": missing_fields,
                    "message": (
                        "HoldingsAsOf source-product metadata is incomplete; holdings "
                        "supportability is partial until core trust metadata is available."
                    ),
                }
            )
        data_quality_status = self._safe_str(source_product.get("data_quality_status")).upper()
        if data_quality_status and data_quality_status != "COMPLETE":
            notes.append(
                {
                    "code": "holdings_as_of_source_quality_not_complete",
                    "severity": "warning",
                    "data_quality_status": data_quality_status,
                    "reason_codes": source_product.get("reason_codes", []),
                    "message": (
                        "lotus-core marked the holdings source-data product as not complete; "
                        "report holdings coverage must remain partial."
                    ),
                }
            )
        reconciliation_status = self._safe_str(source_product.get("reconciliation_status")).upper()
        if reconciliation_status and reconciliation_status not in {"RECONCILED", "COMPLETE"}:
            notes.append(
                {
                    "code": "holdings_as_of_reconciliation_not_complete",
                    "severity": "warning",
                    "reconciliation_status": reconciliation_status,
                    "message": (
                        "lotus-core holdings reconciliation is not complete for this as-of date."
                    ),
                }
            )
        return notes

    def _map_review_transactions(
        self,
        rows: list[dict[str, object]],
        *,
        transaction_result: _TransactionRowsResult | None = None,
    ) -> dict[str, object]:
        transactions_by_asset_class: dict[str, list[dict[str, object]]] = {}
        transactions_by_category: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            asset_class = self._safe_str(row.get("asset_class")) or "UNKNOWN"
            transaction = self._map_review_transaction_row(row)
            transactions_by_asset_class.setdefault(asset_class, []).append(transaction)
            category = self._safe_str(transaction.get("transaction_category")) or "Other"
            transactions_by_category.setdefault(category, []).append(transaction)
        return {
            "transactionsByAssetClass": transactions_by_asset_class,
            "transactionsByCategory": transactions_by_category,
            "transactionCount": len(rows),
            "sourceTransactionCount": transaction_result.source_total
            if transaction_result is not None
            else len(rows),
            "fetchedPageCount": transaction_result.fetched_pages
            if transaction_result is not None
            else 0,
            "supportability": self._transaction_rows_supportability(transaction_result),
            "sourceProduct": self._transaction_source_product(transaction_result),
        }

    @staticmethod
    def _transaction_source_product(
        transaction_result: _TransactionRowsResult | None,
    ) -> dict[str, object]:
        if transaction_result is None:
            return {}
        return transaction_result.source_product

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
            raise ReportingValidationError(
                "allocation_dimensions must be a non-empty list when provided."
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
        }
        for raw_dimension in raw_dimensions:
            if not isinstance(raw_dimension, str) or not raw_dimension.strip():
                raise ReportingValidationError("allocation_dimensions cannot contain blank values.")
            normalized = raw_dimension.strip().lower()
            if normalized not in supported_dimensions:
                raise ReportingValidationError(f"Unsupported allocation dimension: {raw_dimension}")
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
        totals, by_income_type = self._summarize_income_rows(rows)
        return {**totals, "by_income_type": by_income_type}

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
        return (
            await self._list_transaction_rows_result(
                portfolio_id=portfolio_id,
                correlation_id=correlation_id,
                params=params,
            )
        ).rows

    async def _list_transaction_rows_result(
        self,
        *,
        portfolio_id: str,
        correlation_id: str | None,
        params: dict[str, object],
    ) -> _TransactionRowsResult:
        rows: list[dict[str, object]] = []
        skip = 0
        limit = self._to_int(params.get("limit")) or 500
        max_rows = max(1, settings.report_transaction_max_rows)
        max_pages = max(1, settings.report_transaction_max_pages)
        source_total: int | None = None
        fetched_pages = 0
        stop_reason: str | None = None
        source_product: dict[str, object] = {}

        while True:
            if len(rows) >= max_rows:
                stop_reason = "max_rows_reached"
                break
            if fetched_pages >= max_pages:
                stop_reason = "max_pages_reached"
                break
            query_params = dict(params)
            query_params["skip"] = skip
            query_params["limit"] = min(limit, max_rows - len(rows))
            status_code, payload = await self._core_query_client.get_portfolio_transactions(
                portfolio_id=portfolio_id,
                params=query_params,
                correlation_id=correlation_id,
            )
            if status_code < HTTP_BAD_REQUEST:
                fetched_pages += 1
                if not isinstance(payload, dict) or "transactions" not in payload:
                    raise ReportingUpstreamError(
                        "lotus-core transactions payload missing required fields."
                    )
                page_rows = [
                    item
                    for item in self._as_list(payload.get("transactions"))
                    if isinstance(item, dict)
                ]
                remaining_budget = max_rows - len(rows)
                rows.extend(page_rows[:remaining_budget])
                source_total = self._to_int(payload.get("total"))
                source_product = merge_transaction_source_product(
                    current=source_product,
                    payload=payload,
                    returned_count=len(rows),
                    source_total=source_total,
                    fetched_pages=fetched_pages,
                )
                skip += len(page_rows)
                if len(page_rows) > remaining_budget or len(rows) >= max_rows:
                    stop_reason = "max_rows_reached"
                    break
                if not page_rows or source_total is None or skip >= source_total:
                    break
                continue
            if status_code == HTTP_NOT_FOUND:
                raise ReportingNotFoundError(payload.get("detail"))
            if status_code == HTTP_UNPROCESSABLE_ENTITY:
                raise ReportingValidationError(payload.get("detail"))
            raise ReportingUpstreamError(f"lotus-core transactions upstream failure: {payload}")

        return _TransactionRowsResult(
            rows=rows,
            source_total=source_total,
            fetched_pages=fetched_pages,
            supportability=transaction_window_supportability(
                returned_count=len(rows),
                source_total=source_total,
                fetched_pages=fetched_pages,
                stop_reason=stop_reason,
                source_product=source_product,
            ),
            source_product=source_product,
        )

    def _transaction_rows_supportability(
        self, transaction_result: _TransactionRowsResult | None
    ) -> dict[str, object]:
        if transaction_result is None:
            return {"status": "ready", "notes": []}
        return transaction_result.supportability

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

    def _summarize_realized_pnl_rows(self, rows: list[dict[str, object]]) -> dict[str, object]:
        realized_rows = [
            row
            for row in rows
            if self._optional_number_raw(self._realized_pnl_reporting_amount(row)) is not None
        ]
        gains = [
            row
            for row in realized_rows
            if self._to_float(self._realized_pnl_reporting_amount(row)) > 0
        ]
        losses = [
            row
            for row in realized_rows
            if self._to_float(self._realized_pnl_reporting_amount(row)) < 0
        ]
        return {
            "status": "present" if realized_rows else "not_applicable",
            "transaction_count": len(realized_rows),
            "total_realized_pnl_reporting_currency": sum(
                self._to_float(self._realized_pnl_reporting_amount(row)) for row in realized_rows
            ),
            "total_realized_gains_reporting_currency": sum(
                self._to_float(self._realized_pnl_reporting_amount(row)) for row in gains
            ),
            "total_realized_losses_reporting_currency": sum(
                self._to_float(self._realized_pnl_reporting_amount(row)) for row in losses
            ),
            "largest_realized_gain": self._realized_pnl_transaction_key_figure(
                max(gains, key=lambda row: self._to_float(self._realized_pnl_reporting_amount(row)))
            )
            if gains
            else None,
            "largest_realized_loss": self._realized_pnl_transaction_key_figure(
                min(
                    losses,
                    key=lambda row: self._to_float(self._realized_pnl_reporting_amount(row)),
                )
            )
            if losses
            else None,
            "methodology": {
                "source": "lotus-core:/portfolios/{portfolio_id}/transactions",
                "basis": "transaction_level_realized_gain_loss",
                "tax_lot_jurisdiction_treatment": "not_sourced",
            },
        }

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

    # Keep existing monetary-float allowlist line anchors stable.
    # Replace this once the reporting read service migrates money values to Decimal.
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

    def _realized_pnl_reporting_amount(self, row: dict[str, object]) -> object | None:
        for key in (
            "realized_gain_loss_reporting_currency",
            "realized_total_pnl_base",
            "realized_gain_loss",
        ):
            value = row.get(key)
            if value is not None:
                return value
        return None

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
        benchmark_available = False
        resolved_benchmark_code: str | None = None
        resolved_benchmark_source: str | None = None
        for period, row in results_by_period.items():
            row_dict = self._as_dict(row)
            portfolio_twr = self._as_dict(row_dict.get("portfolio_twr"))
            net_summary = self._as_dict(self._as_dict(portfolio_twr.get("net")).get("summary"))
            gross_summary = self._as_dict(self._as_dict(portfolio_twr.get("gross")).get("summary"))
            benchmark = self._as_dict(row_dict.get("benchmark"))
            active = self._as_dict(row_dict.get("active"))
            active_net = self._as_dict(active.get("net"))
            if benchmark:
                benchmark_available = True
                resolved_benchmark_code = (
                    self._safe_str(benchmark.get("benchmark_id")) or resolved_benchmark_code
                )
                resolved_benchmark_source = (
                    self._safe_str(benchmark.get("return_source")) or resolved_benchmark_source
                )
            annualized_supported = self._annualized_return_supported(period)
            summary[period] = {
                "start_date": self._workspace_period_start(row_dict),
                "end_date": self._workspace_period_end(row_dict),
                "net_cumulative_return": self._return_base(net_summary, "cumulative_return"),
                "benchmark_cumulative_return": self._return_base(
                    self._as_dict(benchmark.get("summary")),
                    "cumulative_return",
                ),
                "benchmark_relative_return": self._return_base(active_net, "cumulative_return"),
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
            "monthly_history": self._workspace_performance_history(
                results_by_period=results_by_period,
                period_name="1Y",
                frequency="monthly",
            ),
            "annual_history": self._workspace_performance_history(
                results_by_period=results_by_period,
                period_name="5Y",
                frequency="yearly",
            )
            or self._workspace_performance_history(
                results_by_period=results_by_period,
                period_name="SI",
                frequency="yearly",
            ),
            "benchmark": self._performance_benchmark_context(
                request_payload,
                available=benchmark_available,
                resolved_benchmark_code=resolved_benchmark_code,
                return_source=resolved_benchmark_source,
            ),
            "supportability": self._performance_supportability(
                request_payload,
                benchmark_available=benchmark_available,
            ),
            "methodology": self._review_methodology(request_payload),
        }

    def _annualized_return_supported(self, period: object) -> bool:
        return isinstance(period, str) and period.upper() in {"1Y", "2Y", "5Y", "10Y", "SI"}

    def _review_methodology(self, request_payload: dict[str, object]) -> dict[str, object]:
        benchmark_code = self._normalized_benchmark_code(
            self._optional_string(request_payload, *BENCHMARK_CODE_KEYS)
        )
        return {
            "performance_basis": "NET_AND_GROSS_WHERE_AVAILABLE",
            "benchmark_code": benchmark_code,
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
        periods: list[str] | tuple[dict[str, object], ...],
    ) -> dict[str, object]:
        request: dict[str, object] = {
            "portfolio_id": portfolio_id,
            "report_end_date": as_of_date,
            "input_mode": "stateful",
            "stateful_input": {},
            "periods": [self._workspace_period_request(period) for period in periods],
        }
        reporting_currency = self._optional_string(request_payload, *REPORTING_CURRENCY_KEYS)
        if reporting_currency:
            request["report_ccy"] = reporting_currency
            request["currency"] = reporting_currency
        benchmark_code = self._normalized_benchmark_code(
            self._optional_string(request_payload, *BENCHMARK_CODE_KEYS)
        )
        if benchmark_code:
            request["include_benchmark"] = True
            request["benchmark"] = {
                "benchmark_id": benchmark_code,
                "input_mode": "stateful",
                "return_source": "calculated",
                "stateful_input": {},
            }
        return request

    def _workspace_period_request(self, period: str | dict[str, object]) -> dict[str, object]:
        if isinstance(period, str):
            return {"period": period, "frequencies": ["daily"]}
        period_name = self._safe_str(period.get("period"))
        frequencies = [
            frequency
            for frequency in self._as_list(period.get("frequencies"))
            if isinstance(frequency, str) and frequency
        ]
        return {"period": period_name, "frequencies": frequencies or ["daily"]}

    def _build_contribution_request(
        self,
        *,
        portfolio_id: str,
        as_of_date: str,
        request_payload: dict[str, object],
    ) -> dict[str, object]:
        reporting_currency = (
            self._optional_string(request_payload, *REPORTING_CURRENCY_KEYS) or "USD"
        )
        return {
            "portfolio_id": portfolio_id,
            "report_start_date": f"{as_of_date[:4]}-01-01",
            "report_end_date": as_of_date,
            "analyses": [{"period": "YTD", "frequencies": ["daily"]}],
            "input_mode": "stateful",
            "stateful_input": {
                "metric_basis": "NET",
                "dimensions": ["asset_class", "sector"],
                "include_cash_flows": True,
            },
            "hierarchy": ["asset_class", "sector"],
            "emit": {
                "by_level": True,
                "top_n_per_level": 10,
                "include_other": True,
                "include_unclassified": True,
            },
            "currency": reporting_currency,
            "report_ccy": reporting_currency,
        }

    def _map_performance_contribution(
        self,
        *,
        status_code: int,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if not self._contribution_ready(status_code, payload):
            return {
                "status": "unavailable",
                "reason_code": "contribution_not_sourced",
                "message": (
                    "Position and hierarchy contribution were not returned by lotus-performance."
                ),
            }
        results_by_period = self._as_dict(payload.get("results_by_period"))
        ytd = self._as_dict(results_by_period.get("YTD"))
        return {
            "status": "present",
            "period": "YTD",
            "total_portfolio_return_pct": ytd.get("total_portfolio_return"),
            "total_contribution_pct": ytd.get("total_contribution"),
            "summary": self._as_dict(ytd.get("summary")),
            "top_position_contributors": self._map_position_contributions(
                self._as_list(ytd.get("position_contributions"))
            ),
            "hierarchy": self._map_contribution_levels(self._as_list(ytd.get("levels"))),
            "methodology": {
                "source": "lotus-performance:/performance/contribution",
                "basis": "NET",
                "frequency": "daily",
                "weighting_scheme": self._as_dict(ytd.get("summary")).get("weighting_scheme"),
                # lotus-performance computes `residual = total_portfolio_return -
                # sum_of_contributions` and may allocate it back into the rows.
                # Whether it did decides whether a ranking can be read as summing
                # to the portfolio return, so the flag is methodology, not
                # diagnostics: without it a reconciliation line can state the
                # residual but not what it means (issue #209).
                "residual_allocation_applied": self._as_dict(ytd.get("smoothing_evidence")).get(
                    "residual_allocation_applied"
                ),
                "residual_allocation_basis": self._as_dict(ytd.get("smoothing_evidence")).get(
                    "residual_allocation_basis"
                ),
            },
            "diagnostics": payload.get("diagnostics"),
            "audit": payload.get("audit"),
        }

    @staticmethod
    def _contribution_ready(status_code: int, payload: dict[str, object]) -> bool:
        return status_code < HTTP_BAD_REQUEST and "results_by_period" in payload

    def _map_position_contributions(self, rows: list[object]) -> list[dict[str, object]]:
        return map_position_contributions(rows)

    def _map_contribution_levels(self, levels: list[object]) -> list[dict[str, object]]:
        return map_contribution_levels(levels, to_int=self._to_int)

    @staticmethod
    def _security_id_from_position_id(position_id: str) -> str:
        return security_id_from_position_id(position_id)

    def _enrich_holdings_with_contribution(
        self,
        *,
        response: dict[str, object],
        contribution: dict[str, object],
    ) -> None:
        if contribution.get("status") != "present":
            return
        contribution_by_security = {
            self._safe_str(row.get("security_id")): row
            for row in [
                self._as_dict(item)
                for item in self._as_list(contribution.get("top_position_contributors"))
            ]
            if self._safe_str(row.get("security_id"))
        }
        holdings = self._as_dict(response.get("holdings"))
        for rows in self._as_dict(holdings.get("holdingsByAssetClass")).values():
            for row_payload in self._as_list(rows):
                row = self._as_dict(row_payload)
                contribution_row = self._as_dict(
                    contribution_by_security.get(self._safe_str(row.get("security_id")))
                )
                if not contribution_row:
                    continue
                row["ytd_contribution_pct"] = contribution_row.get("total_contribution_pct")
                row["ytd_average_weight_pct"] = contribution_row.get("average_weight_pct")
                row["ytd_total_return_pct"] = contribution_row.get("total_return_pct")

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
        return self._workspace_breakdowns(period_payload, frequency="daily")

    def _workspace_breakdowns(
        self, period_payload: dict[str, object], *, frequency: str
    ) -> list[object]:
        portfolio_twr = self._as_dict(period_payload.get("portfolio_twr"))
        net_block = self._as_dict(portfolio_twr.get("net"))
        breakdowns = self._as_dict(net_block.get("breakdowns"))
        items = breakdowns.get(frequency)
        return items if isinstance(items, list) else []

    def _workspace_performance_history(
        self,
        *,
        results_by_period: dict[str, object],
        period_name: str,
        frequency: str,
    ) -> list[dict[str, object]]:
        period_payload = self._as_dict(results_by_period.get(period_name))
        history: list[dict[str, object]] = []
        cumulative_value = 0.0
        for item in self._workspace_breakdowns(period_payload, frequency=frequency):
            row = self._as_dict(item)
            economics = self._as_dict(row.get("economics"))
            begin_market_value = self._to_float(economics.get("begin_market_value"))
            end_market_value = self._to_float(economics.get("end_market_value"))
            beginning_cash_flow = self._to_float(economics.get("beginning_cash_flow"))
            ending_cash_flow = self._to_float(economics.get("ending_cash_flow"))
            net_cash_flow = self._to_float(economics.get("net_cash_flow"))
            flow_adjusted_end_value = self._to_float(
                economics.get("flow_adjusted_end_market_value")
            )
            performance_value = flow_adjusted_end_value - begin_market_value
            cumulative_value += performance_value
            inflows = sum(
                amount for amount in (beginning_cash_flow, ending_cash_flow) if amount > 0
            )
            outflows = sum(
                amount for amount in (beginning_cash_flow, ending_cash_flow) if amount < 0
            )
            history.append(
                {
                    "period": self._safe_str(row.get("period")),
                    "period_start": self._safe_str(row.get("period_start")),
                    "period_end": self._safe_str(row.get("period_end")),
                    "begin_market_value": begin_market_value,
                    "end_market_value": end_market_value,
                    "inflows": inflows,
                    "outflows": outflows,
                    "net_cash_flow": net_cash_flow,
                    "performance_value": performance_value,
                    "cumulative_performance_value": cumulative_value,
                    "twr_pct": self._return_base(row, "period_return"),
                    "cumulative_twr_pct": self._return_base(row, "cumulative_return"),
                    "annualized_twr_pct": self._return_base(row, "annualized_return"),
                }
            )
        return history

    def _workspace_portfolio_open_date(self, payload: dict[str, object]) -> str | None:
        results_by_period = self._as_dict(payload.get("results_by_period"))
        for period_name in ("SI", "5Y", "YTD", "3M", "1M"):
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
        raise ReportingValidationError(f"Missing required request field: {keys[0]}")

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

    def _review_period(self, as_of_date: str) -> dict[str, object]:
        return {
            "label": "YTD",
            "start_date": f"{as_of_date[:4]}-01-01",
            "end_date": as_of_date,
        }

    def _reporting_currency(
        self, request_payload: dict[str, object], summary: dict[str, object]
    ) -> str | None:
        requested = self._optional_string(request_payload, *REPORTING_CURRENCY_KEYS)
        if requested:
            return requested
        metadata = self._as_dict(summary.get("snapshot_metadata"))
        return self._safe_str(summary.get("reporting_currency") or metadata.get("currency")) or None

    def _review_audience(self, client_sections: list[dict[str, object]]) -> dict[str, object]:
        section_statuses = {
            self._safe_str(section.get("section_id")): self._safe_str(section.get("status"))
            for section in client_sections
        }
        return {
            "primary": "client_advisor",
            "client_ready": all(
                status in {"ready", "not_applicable", "omitted_by_request"}
                for status in section_statuses.values()
            ),
            "advisor_only_sections_present": True,
            "section_statuses": section_statuses,
        }

    def _review_disclosures(self, response: dict[str, object]) -> list[dict[str, object]]:
        disclosures: list[dict[str, object]] = [
            {
                "disclosure_id": "performance_methodology",
                "severity": "standard",
                "text": (
                    "Performance figures use source-provided net and gross time-weighted "
                    "returns where available; sub-year annualized returns are suppressed unless "
                    "source support is explicit."
                ),
            },
            {
                "disclosure_id": "risk_methodology",
                "severity": "standard",
                "text": (
                    "Risk figures are calculated from sourced net daily return history and "
                    "should be reviewed with section supportability notes before client use."
                ),
            },
            {
                "disclosure_id": "reporting_view",
                "severity": "standard",
                "text": (
                    "Holdings and transactions are reporting views for portfolio review and "
                    "may differ from official custody statements."
                ),
            },
        ]
        if self._safe_str(self._as_dict(response.get("readiness")).get("status")) != "ready":
            disclosures.append(
                {
                    "disclosure_id": "partial_supportability",
                    "severity": "supportability",
                    "text": (
                        "One or more requested sections are partial or unavailable and require "
                        "advisor review before client presentation."
                    ),
                }
            )
        return disclosures

    def _review_key_figures(self, response: dict[str, object]) -> dict[str, object]:
        overview = self._as_dict(response.get("overview"))
        allocation = self._as_dict(response.get("allocation"))
        performance = self._as_dict(response.get("performance"))
        risk = self._as_dict(response.get("riskAnalytics"))
        income_activity = self._as_dict(response.get("incomeAndActivity"))
        holdings = self._as_dict(response.get("holdings"))
        transactions = self._as_dict(response.get("transactions"))
        client_profile = self._as_dict(response.get("clientProfile"))
        currency = self._safe_str(response.get("reportingCurrency") or overview.get("currency"))
        total_market_value = self._to_float(overview.get("total_market_value"))
        total_cash = self._to_float(overview.get("total_cash"))
        top_asset_class = self._top_bucket(self._as_list(allocation.get("byAssetClass")))
        top_currency = self._top_bucket(self._as_list(allocation.get("byCurrency")))
        return {
            "conventions": {
                "currency": currency,
                "monetary_fields": "reporting currency amounts use *_reporting_currency names",
                "percentage_fields": "normalized key figure percentages use *_pct names",
                "legacy_weight_fields": (
                    "section allocation and holding weights remain decimal ratios"
                ),
            },
            "client_profile": self._client_profile_key_figures(client_profile),
            "portfolio_value": {
                "total_market_value_reporting_currency": total_market_value,
                "invested_market_value_reporting_currency": self._to_float(
                    overview.get("invested_market_value")
                ),
                "cash_balance_reporting_currency": total_cash,
                "cash_weight_pct": self._safe_pct(total_cash, total_market_value),
            },
            "allocation": {
                "largest_asset_class": self._bucket_key_figure(top_asset_class),
                "largest_currency": self._bucket_key_figure(top_currency),
                "asset_class_count": len(self._as_list(allocation.get("byAssetClass"))),
                "currency_count": len(self._as_list(allocation.get("byCurrency"))),
            },
            "performance": self._performance_key_figures(performance),
            "risk": self._risk_key_figures(risk),
            "income_and_activity": self._income_activity_key_figures(income_activity),
            "holdings": self._holdings_key_figures(holdings),
            "transactions": self._transaction_key_figures(transactions),
        }

    def _client_profile_key_figures(self, client_profile: dict[str, object]) -> dict[str, object]:
        identity = self._as_dict(client_profile.get("identity"))
        mandate = self._as_dict(client_profile.get("mandate_profile"))
        portfolio_profile = self._as_dict(client_profile.get("portfolio_profile"))
        return {
            "profile_status": client_profile.get("status", "unavailable"),
            "client_id": identity.get("client_id"),
            "advisor_id": identity.get("advisor_id"),
            "booking_center_code": identity.get("booking_center_code"),
            "portfolio_type": mandate.get("portfolio_type"),
            "objective": mandate.get("objective"),
            "risk_exposure": mandate.get("risk_exposure"),
            "investment_time_horizon": mandate.get("investment_time_horizon"),
            "base_currency": portfolio_profile.get("base_currency"),
            "open_date": portfolio_profile.get("open_date"),
            "is_leverage_allowed": mandate.get("is_leverage_allowed"),
            "cost_basis_method": mandate.get("cost_basis_method"),
        }

    def _performance_key_figures(self, performance: dict[str, object]) -> dict[str, object]:
        summary = self._as_dict(performance.get("summary"))
        benchmark = self._as_dict(performance.get("benchmark"))
        contribution = self._as_dict(performance.get("contribution"))
        top_contributors = self._as_list(contribution.get("top_position_contributors"))
        return {
            "one_month_net_return_pct": self._period_return(summary, "1M", "net_cumulative_return"),
            "three_month_net_return_pct": self._period_return(
                summary, "3M", "net_cumulative_return"
            ),
            "ytd_net_return_pct": self._period_return(summary, "YTD", "net_cumulative_return"),
            "ytd_benchmark_return_pct": self._period_return(
                summary, "YTD", "benchmark_cumulative_return"
            ),
            "ytd_benchmark_relative_return_pct": self._period_return(
                summary, "YTD", "benchmark_relative_return"
            ),
            "one_year_net_return_pct": self._period_return(summary, "1Y", "net_cumulative_return"),
            "five_year_net_annualized_return_pct": self._period_return(
                summary, "5Y", "net_annualized_return"
            ),
            "since_inception_net_return_pct": self._period_return(
                summary, "SI", "net_cumulative_return"
            ),
            "benchmark_code": benchmark.get("benchmark_code"),
            "benchmark_comparison_status": benchmark.get("comparison_status"),
            "contribution_status": contribution.get("status", "not_requested"),
            "ytd_total_contribution_pct": contribution.get("total_contribution_pct"),
            "largest_positive_contributor": self._contribution_extreme(
                top_contributors, largest=True
            ),
            "largest_negative_contributor": self._contribution_extreme(
                top_contributors, largest=False
            ),
        }

    def _risk_key_figures(self, risk: dict[str, object]) -> dict[str, object]:
        summary = self._as_dict(risk.get("summary"))
        ytd = self._as_dict(summary.get("YTD"))
        one_year = self._as_dict(summary.get("1Y"))
        return {
            "ytd_volatility_pct": ytd.get("volatility"),
            "ytd_drawdown_pct": ytd.get("drawdown"),
            "ytd_value_at_risk_pct": ytd.get("value_at_risk"),
            "ytd_expected_shortfall_pct": self._expected_shortfall(risk, "YTD"),
            "ytd_risk_adjusted_return": ytd.get("risk_adjusted_return"),
            "ytd_beta": ytd.get("beta"),
            "ytd_tracking_error_pct": ytd.get("tracking_error"),
            "ytd_information_ratio": ytd.get("information_ratio"),
            "one_year_volatility_pct": one_year.get("volatility"),
            "one_year_drawdown_pct": one_year.get("drawdown"),
            "one_year_value_at_risk_pct": one_year.get("value_at_risk"),
            "one_year_expected_shortfall_pct": self._expected_shortfall(risk, "1Y"),
            "one_year_beta": one_year.get("beta"),
            "one_year_tracking_error_pct": one_year.get("tracking_error"),
            "one_year_information_ratio": one_year.get("information_ratio"),
            "benchmark_relative_status": self._risk_benchmark_status(risk),
        }

    def _income_activity_key_figures(self, income_activity: dict[str, object]) -> dict[str, object]:
        income = self._as_dict(income_activity.get("incomeSummary"))
        activity = self._as_dict(income_activity.get("activitySummary"))
        realized_pnl = self._as_dict(income_activity.get("realizedPnlSummary"))
        supportability = self._as_dict(income_activity.get("supportability"))
        return {
            "supportability_status": supportability.get("status", "ready"),
            "gross_income_reporting_currency": self._to_float(
                income.get("gross_amount_reporting_currency")
            ),
            "net_income_reporting_currency": self._to_float(
                income.get("net_amount_reporting_currency")
            ),
            "withholding_tax_reporting_currency": self._to_float(
                income.get("withholding_tax_reporting_currency")
            ),
            "income_transaction_count": self._to_int(income.get("transaction_count")),
            "total_inflows_reporting_currency": self._to_float(activity.get("total_inflows")),
            "total_outflows_reporting_currency": self._to_float(activity.get("total_outflows")),
            "total_fees_reporting_currency": self._to_float(activity.get("total_fees")),
            "total_taxes_reporting_currency": self._to_float(activity.get("total_taxes")),
            "realized_pnl_status": realized_pnl.get("status", "not_applicable"),
            "total_realized_pnl_reporting_currency": self._to_float(
                realized_pnl.get("total_realized_pnl_reporting_currency")
            ),
            "realized_pnl_transaction_count": self._to_int(realized_pnl.get("transaction_count")),
        }

    def _holdings_key_figures(self, holdings: dict[str, object]) -> dict[str, object]:
        rows = self._holding_rows(holdings)
        supportability = self._as_dict(holdings.get("supportability"))
        source_product = self._as_dict(holdings.get("sourceProduct"))
        positive_rows = [
            row for row in rows if self._to_float(row.get("market_value_reporting_currency")) > 0
        ]
        sorted_rows = sorted(
            positive_rows,
            key=lambda row: self._to_float(row.get("market_value_reporting_currency")),
            reverse=True,
        )
        positive_exposure = sum(
            self._to_float(row.get("market_value_reporting_currency")) for row in sorted_rows
        )
        unrealized_pnl_rows = [
            row
            for row in rows
            if self._optional_number_raw(row.get("unrealized_pnl_reporting_currency")) is not None
        ]
        total_unrealized_pnl = sum(
            self._to_float(row.get("unrealized_pnl_reporting_currency"))
            for row in unrealized_pnl_rows
        )
        cost_basis_rows = [
            row
            for row in rows
            if self._optional_number_raw(row.get("cost_basis_reporting_currency")) is not None
        ]
        negative_cash_rows = [
            row
            for row in rows
            if self._safe_str(row.get("asset_class")) == "Cash"
            and self._to_float(row.get("market_value_reporting_currency")) < 0
        ]
        return {
            "supportability_status": supportability.get("status", "ready"),
            "source_product": source_product,
            "source_data_quality_status": source_product.get("data_quality_status"),
            "source_reconciliation_status": source_product.get("reconciliation_status"),
            "latest_evidence_timestamp": source_product.get("latest_evidence_timestamp"),
            "position_count": self._to_int(holdings.get("positionCount")),
            "positive_exposure_reporting_currency": positive_exposure,
            "cost_basis_coverage": self._coverage_status(len(cost_basis_rows), len(rows)),
            "unrealized_pnl_coverage": self._coverage_status(len(unrealized_pnl_rows), len(rows)),
            "total_unrealized_pnl_reporting_currency": (
                total_unrealized_pnl if unrealized_pnl_rows else None
            ),
            "total_unrealized_pnl_pct": self._safe_pct(
                total_unrealized_pnl,
                sum(
                    abs(self._to_float(row.get("cost_basis_reporting_currency")))
                    for row in cost_basis_rows
                ),
            )
            if cost_basis_rows and unrealized_pnl_rows
            else None,
            "top_holding": self._holding_key_figure(sorted_rows[0]) if sorted_rows else None,
            "top_five_positive_exposure_pct": self._safe_pct(
                sum(
                    self._to_float(row.get("market_value_reporting_currency"))
                    for row in sorted_rows[:5]
                ),
                positive_exposure,
            ),
            "top_ten_positive_exposure_pct": self._safe_pct(
                sum(
                    self._to_float(row.get("market_value_reporting_currency"))
                    for row in sorted_rows[:10]
                ),
                positive_exposure,
            ),
            "negative_cash_position_count": len(negative_cash_rows),
            "largest_negative_cash": (
                self._holding_key_figure(
                    min(
                        negative_cash_rows,
                        key=lambda row: self._to_float(row.get("market_value_reporting_currency")),
                    )
                )
                if negative_cash_rows
                else None
            ),
        }

    @staticmethod
    def _coverage_status(populated_count: int, total_count: int) -> str:
        if total_count <= 0:
            return "not_sourced"
        if populated_count == total_count:
            return "present"
        if populated_count > 0:
            return "partial"
        return "not_sourced"

    def _transaction_key_figures(self, transactions: dict[str, object]) -> dict[str, object]:
        by_category = self._as_dict(transactions.get("transactionsByCategory"))
        supportability = self._as_dict(transactions.get("supportability"))
        source_product = self._as_dict(transactions.get("sourceProduct"))
        rows = [
            self._as_dict(row)
            for category_rows in by_category.values()
            for row in self._as_list(category_rows)
        ]
        realized_rows = [
            row
            for row in rows
            if self._optional_number_raw(row.get("realized_pnl_reporting_currency")) is not None
        ]
        counts = {
            category: len(self._as_list(rows))
            for category, rows in by_category.items()
            if isinstance(category, str)
        }
        return {
            "supportability_status": supportability.get("status", "ready"),
            "transaction_count": self._to_int(transactions.get("transactionCount")),
            "source_transaction_count": transactions.get("sourceTransactionCount"),
            "fetched_page_count": transactions.get("fetchedPageCount"),
            "source_product": source_product,
            "source_data_quality_status": source_product.get("data_quality_status"),
            "source_reconciliation_status": source_product.get("reconciliation_status"),
            "latest_evidence_timestamp": source_product.get("latest_evidence_timestamp"),
            "transaction_count_by_category": counts,
            "realized_pnl_status": "present" if realized_rows else "not_applicable",
            "realized_pnl_transaction_count": len(realized_rows),
            "total_realized_pnl_reporting_currency": sum(
                self._to_float(row.get("realized_pnl_reporting_currency")) for row in realized_rows
            ),
        }

    def _realized_pnl_transaction_key_figure(self, row: dict[str, object]) -> dict[str, object]:
        return {
            "transaction_id": row.get("transaction_id"),
            "transaction_date": row.get("transaction_date"),
            "transaction_type": row.get("transaction_type"),
            "security_id": row.get("security_id"),
            "instrument_id": row.get("instrument_id"),
            "realized_pnl_reporting_currency": self._to_float(
                self._realized_pnl_reporting_amount(row)
            ),
        }

    def _review_observations(self, response: dict[str, object]) -> list[dict[str, object]]:
        observations: list[dict[str, object]] = []
        key_figures = self._as_dict(response.get("keyFigures"))
        performance = self._as_dict(key_figures.get("performance"))
        holdings = self._as_dict(key_figures.get("holdings"))
        transactions = self._as_dict(response.get("transactions"))
        transaction_supportability = self._as_dict(transactions.get("supportability"))
        risk = self._as_dict(response.get("riskAnalytics"))
        client_profile = self._as_dict(response.get("clientProfile"))
        missing_profile_fields = [
            self._safe_str(field) for field in self._as_list(client_profile.get("missing_fields"))
        ]
        if missing_profile_fields:
            observations.append(
                {
                    "observation_id": "client_profile_incomplete",
                    "severity": "gap",
                    "message": (
                        "Client and mandate profile is incomplete for review preparation: "
                        + ", ".join(missing_profile_fields)
                        + "."
                    ),
                    "source_section_ids": ["client_profile"],
                }
            )
        observations.append(
            {
                "observation_id": "suitability_and_mandate_controls_not_sourced",
                "severity": "gap",
                "message": (
                    "Suitability assessment, target allocation, mandate guidelines, liquidity "
                    "needs, and regulatory product disclosure controls are not sourced in this "
                    "report response and require advisory or management-domain integration."
                ),
                "source_section_ids": ["client_profile", "asset_allocation", "holdings_appendix"],
            }
        )
        if performance.get("benchmark_comparison_status") == "unavailable":
            observations.append(
                {
                    "observation_id": "benchmark_comparison_not_sourced",
                    "severity": "watch",
                    "message": (
                        "Benchmark code was supplied but benchmark return series is not sourced, "
                        "so excess return and benchmark-relative risk are not available."
                    ),
                    "source_section_ids": ["performance_review", "risk_review"],
                }
            )
        if performance.get("contribution_status") not in {"present", None, "not_requested"}:
            observations.append(
                {
                    "observation_id": "performance_contribution_unavailable",
                    "severity": "gap",
                    "message": (
                        "Position and hierarchy contribution were requested but are not available "
                        "in the current report response."
                    ),
                    "source_section_ids": ["performance_review"],
                }
            )
        if holdings.get("unrealized_pnl_coverage") != "present":
            observations.append(
                {
                    "observation_id": "position_unrealized_pnl_incomplete",
                    "severity": "gap",
                    "message": (
                        "Not all holdings include source-backed cost basis and unrealized P&L."
                    ),
                    "source_section_ids": ["holdings_appendix"],
                }
            )
        holdings_supportability = self._as_dict(
            self._as_dict(response.get("holdings")).get("supportability")
        )
        if holdings_supportability.get("status") == "partial":
            observations.append(
                {
                    "observation_id": "holdings_source_quality_partial",
                    "severity": "watch",
                    "message": self._supportability_message(
                        holdings_supportability,
                        "Holdings Appendix",
                    ),
                    "source_section_ids": ["holdings_appendix"],
                }
            )
        if transaction_supportability.get("status") == "partial":
            observations.append(
                {
                    "observation_id": "transaction_window_truncated",
                    "severity": "watch",
                    "message": self._supportability_message(
                        transaction_supportability,
                        "Transactions Appendix",
                    ),
                    "source_section_ids": [
                        "income_cash_activity",
                        "transactions_appendix",
                    ],
                }
            )
        ytd_return = self._optional_number_raw(performance.get("ytd_net_return_pct"))
        if ytd_return is not None and ytd_return < 0:
            observations.append(
                {
                    "observation_id": "negative_ytd_performance",
                    "severity": "watch",
                    "message": f"YTD net return is negative at {ytd_return:.2f}%.",
                    "source_section_ids": ["performance_review"],
                }
            )
        negative_cash_count = self._to_int(holdings.get("negative_cash_position_count"))
        if negative_cash_count:
            observations.append(
                {
                    "observation_id": "negative_cash_position",
                    "severity": "watch",
                    "message": (
                        f"{negative_cash_count} cash position(s) have negative market value."
                    ),
                    "source_section_ids": ["holdings_appendix"],
                }
            )
        top_five_concentration = self._optional_number_raw(
            holdings.get("top_five_positive_exposure_pct")
        )
        if top_five_concentration is not None and top_five_concentration >= 50:
            observations.append(
                {
                    "observation_id": "top_five_positive_exposure",
                    "severity": "watch",
                    "message": (
                        f"Top five positive positions represent {top_five_concentration:.2f}% "
                        "of positive portfolio exposure."
                    ),
                    "source_section_ids": ["holdings_appendix"],
                }
            )
        for note in self._as_list(self._as_dict(risk.get("supportability")).get("notes")):
            note_payload = self._as_dict(note)
            if self._safe_str(note_payload.get("severity")) != "warning":
                continue
            observations.append(
                {
                    "observation_id": self._safe_str(note_payload.get("code")),
                    "severity": "watch",
                    "message": self._safe_str(note_payload.get("message")),
                    "source_section_ids": ["risk_review"],
                }
            )
        return observations

    def _report_structure(
        self,
        response: dict[str, object],
        client_sections: list[dict[str, object]],
    ) -> dict[str, object]:
        section_statuses = {
            self._safe_str(section.get("section_id")): self._safe_str(section.get("status"))
            for section in client_sections
        }
        return {
            "status": "ready",
            "presentation_sequence": [
                {
                    "sequence": 1,
                    "section_key": "client_and_mandate_context",
                    "title": "Client, Mandate, And Meeting Context",
                    "source_section_ids": ["client_profile", "executive_summary"],
                },
                {
                    "sequence": 2,
                    "section_key": "portfolio_snapshot",
                    "title": "Portfolio Snapshot And Allocation",
                    "source_section_ids": ["executive_summary", "asset_allocation"],
                },
                {
                    "sequence": 3,
                    "section_key": "performance_drivers",
                    "title": "Performance, Contribution, And P&L Drivers",
                    "source_section_ids": ["performance_review", "holdings_appendix"],
                },
                {
                    "sequence": 4,
                    "section_key": "risk_and_suitability_controls",
                    "title": "Risk, Concentration, Suitability, And Mandate Controls",
                    "source_section_ids": [
                        "risk_review",
                        "asset_allocation",
                        "holdings_appendix",
                    ],
                },
                {
                    "sequence": 5,
                    "section_key": "income_cash_and_activity",
                    "title": "Income, Cash, Fees, Taxes, And Activity",
                    "source_section_ids": ["income_cash_activity", "transactions_appendix"],
                },
                {
                    "sequence": 6,
                    "section_key": "appendices_and_evidence",
                    "title": "Holdings, Transactions, Methodology, And Evidence",
                    "source_section_ids": [
                        "holdings_appendix",
                        "transactions_appendix",
                        "evidence",
                    ],
                },
            ],
            "client_section_statuses": section_statuses,
            "advisor_only_sections": ["advisor_discussion", "advisorBriefing", "aiReadiness"],
            "machine_readable_payloads": [
                "clientProfile",
                "keyFigures",
                "reviewObservations",
                "reportCoverage",
                "evidence",
            ],
        }

    def _advisor_briefing(self, response: dict[str, object]) -> dict[str, object]:
        profile = self._as_dict(response.get("clientProfile"))
        identity = self._as_dict(profile.get("identity"))
        mandate = self._as_dict(profile.get("mandate_profile"))
        key_figures = self._as_dict(response.get("keyFigures"))
        performance = self._as_dict(key_figures.get("performance"))
        holdings = self._as_dict(key_figures.get("holdings"))
        risk = self._as_dict(key_figures.get("risk"))
        currency = self._safe_str(response.get("reportingCurrency")) or self._safe_str(
            self._as_dict(key_figures.get("conventions")).get("currency")
        )
        briefing_items: list[dict[str, object]] = [
            {
                "briefing_id": "client_context",
                "title": "Confirm client context before discussion",
                "talking_points": [
                    f"Client {identity.get('client_id') or 'unknown client'} is covered by advisor "
                    f"{identity.get('advisor_id') or 'unknown advisor'}.",
                    f"Mandate type is {mandate.get('portfolio_type') or 'not sourced'} with "
                    f"{mandate.get('risk_exposure') or 'not sourced'} risk exposure.",
                    self._briefing_sentence(
                        "Objective",
                        self._safe_str(mandate.get("objective")) or "not sourced",
                    ),
                ],
                "source_section_ids": ["client_profile"],
            },
            {
                "briefing_id": "performance_drivers",
                "title": "Explain performance using contribution and P&L",
                "talking_points": [
                    "YTD net return is "
                    + self._display_pct(performance.get("ytd_net_return_pct"))
                    + ".",
                    "Use largest positive and negative contributors to explain return drivers.",
                    "Total unrealized P&L is "
                    + self._display_money(
                        holdings.get("total_unrealized_pnl_reporting_currency"),
                        currency=currency,
                    )
                    + ".",
                ],
                "source_section_ids": ["performance_review", "holdings_appendix"],
            },
            {
                "briefing_id": "risk_controls",
                "title": "Review risk, concentration, and suitability gaps",
                "talking_points": [
                    "YTD volatility is " + self._display_pct(risk.get("ytd_volatility_pct")) + ".",
                    "Top five positive exposure is "
                    + self._display_pct(holdings.get("top_five_positive_exposure_pct"))
                    + ".",
                    "Suitability, mandate guideline, and liquidity-need controls are not sourced "
                    "in this report and must be checked in the advisory or management workflow.",
                ],
                "source_section_ids": ["risk_review", "holdings_appendix", "client_profile"],
            },
        ]
        return {
            "status": "ready",
            "audience": "advisor_only",
            "briefing_items": briefing_items,
            "required_advisor_checks": [
                {
                    "check_id": "confirm_client_profile_current",
                    "status": profile.get("status", "unavailable"),
                    "required": True,
                    "source_section_ids": ["client_profile"],
                },
                {
                    "check_id": "review_suitability_and_mandate_controls",
                    "status": "not_sourced",
                    "required": True,
                    "source_section_ids": ["client_profile", "asset_allocation"],
                },
                {
                    "check_id": "confirm_benchmark_and_risk_supportability",
                    "status": self._benchmark_comparison_coverage(response),
                    "required": True,
                    "source_section_ids": ["performance_review", "risk_review"],
                },
            ],
        }

    def _briefing_sentence(self, label: str, value: str) -> str:
        cleaned = value.strip()
        if cleaned.endswith("."):
            return f"{label}: {cleaned}"
        return f"{label}: {cleaned}."

    def _display_pct(self, value: object) -> str:
        parsed = self._optional_number_raw(value)
        if parsed is None:
            return "not sourced"
        return f"{parsed:.2f}%"

    def _display_money(self, value: object, *, currency: str) -> str:
        parsed = self._optional_number_raw(value)
        if parsed is None:
            return "not sourced"
        prefix = f"{currency} " if currency else ""
        return f"{prefix}{parsed:,.2f}"

    def _ai_readiness(self, response: dict[str, object]) -> dict[str, object]:
        ai_status = self._ai_assistance_coverage(response)
        return {
            "status": ai_status,
            "mode": "grounded_assistance_metadata_only",
            "supported_ai_features": [
                {
                    "feature_id": "meeting_question_suggestions",
                    "status": "ready" if ai_status == "guarded_ready" else "partial",
                    "requires_human_approval": True,
                },
                {
                    "feature_id": "plain_language_section_summary_draft",
                    "status": "ready" if ai_status == "guarded_ready" else "partial",
                    "requires_human_approval": True,
                },
                {
                    "feature_id": "exception_explanation_draft",
                    "status": "ready" if response.get("reviewObservations") else "not_applicable",
                    "requires_human_approval": True,
                },
            ],
            "blocked_ai_features": [
                {
                    "feature_id": "trade_recommendation",
                    "reason": (
                        "Requires governed advisory workflow, suitability evidence, "
                        "and advisor approval."
                    ),
                },
                {
                    "feature_id": "suitability_determination",
                    "reason": (
                        "Suitability is not sourced by this report and must remain in "
                        "governed advisory controls."
                    ),
                },
                {
                    "feature_id": "client_profile_inference",
                    "reason": (
                        "Client profile must come from an authoritative source, not "
                        "model inference."
                    ),
                },
            ],
            "required_grounding_payloads": [
                "clientProfile",
                "keyFigures",
                "reviewObservations",
                "reportCoverage",
                "evidence",
            ],
            "control_requirements": [
                "Use only report evidence and cited source sections for generated text.",
                "Preserve advisor-only separation for prompts, follow-ups, and limitations.",
                "Do not turn report gaps into advice or product recommendations.",
            ],
        }

    def _review_report_coverage(self, response: dict[str, object]) -> dict[str, object]:
        key_figures = self._as_dict(response.get("keyFigures"))
        return {
            "status": self._as_dict(response.get("readiness")).get("status", "ready"),
            "figure_groups": [
                {
                    "group_id": "client_profile",
                    "status": self._client_profile_coverage(response),
                    "required": True,
                    "message": (
                        "Client id, advisor id, booking center, objective, risk exposure, "
                        "investment horizon, mandate type, leverage permission, and cost basis "
                        "method are sourced from lotus-core portfolio detail."
                    ),
                },
                self._figure_group("portfolio_value", key_figures, required=True),
                self._figure_group("allocation", key_figures, required=True),
                self._figure_group("performance", key_figures, required=True),
                self._figure_group("risk", key_figures, required=True),
                self._figure_group("income_and_activity", key_figures, required=False),
                self._figure_group("holdings", key_figures, required=True),
                self._figure_group("transactions", key_figures, required=False),
                {
                    "group_id": "benchmark_comparison",
                    "status": self._benchmark_comparison_coverage(response),
                    "required": True,
                    "message": (
                        "Benchmark excess return, tracking error, information ratio, beta, "
                        "and benchmark-relative risk require sourced benchmark return series."
                    ),
                },
                {
                    "group_id": "position_pnl_and_cost_basis",
                    "status": self._position_pnl_coverage(response),
                    "required": True,
                    "message": (
                        "Position-level book cost and unrealized gain/loss are sourced from "
                        "lotus-core HoldingsAsOf and must be present for a complete review pack."
                    ),
                },
                {
                    "group_id": "performance_contribution",
                    "status": self._contribution_coverage(response),
                    "required": True,
                    "message": (
                        "Position, asset-class, and sector contribution are sourced from "
                        "lotus-performance /performance/contribution for YTD review attribution."
                    ),
                },
                {
                    "group_id": "transaction_realized_gain_loss",
                    "status": self._transaction_realized_pnl_coverage(response),
                    "required": False,
                    "message": (
                        "Transaction-level realized gain/loss is sourced from lotus-core "
                        "TransactionLedgerWindow when disposal or realized-P&L rows are present."
                    ),
                },
                {
                    "group_id": "instrument_reference_data",
                    "status": self._instrument_reference_coverage(response),
                    "required": True,
                    "message": (
                        "Security identifier, ISIN, product type, sector, country of risk, "
                        "rating, liquidity tier, and holding date are required where sourced."
                    ),
                },
                {
                    "group_id": "targets_guidelines_and_suitability",
                    "status": "not_sourced",
                    "required": True,
                    "message": (
                        "Risk profile and objective are sourced from lotus-core client profile "
                        "where available. Target allocation, mandate guidelines, liquidity "
                        "needs, product restrictions, and suitability posture are owned by "
                        "advisory or management domains and are not invented by lotus-report."
                    ),
                },
                {
                    "group_id": "advisor_ai_assistance",
                    "status": self._ai_assistance_coverage(response),
                    "required": False,
                    "message": (
                        "AI assistance is represented as guarded readiness metadata only. "
                        "LLM-generated advice, trade recommendations, and suitability decisions "
                        "require governed lotus-ai integration and human advisor approval."
                    ),
                },
                {
                    "group_id": "tax_lot_and_jurisdiction_tax_treatment",
                    "status": "not_sourced",
                    "required": False,
                    "message": (
                        "Open tax-lot reporting, lot-level realized gain/loss attribution, and "
                        "jurisdiction-specific tax treatment are not sourced in the current "
                        "report response."
                    ),
                },
            ],
        }

    def _upstream_capability_audit(self, response: dict[str, object]) -> dict[str, object]:
        coverage_groups: dict[str, dict[str, object]] = {}
        for group_payload in self._as_list(
            self._as_dict(response.get("reportCoverage")).get("figure_groups")
        ):
            group = self._as_dict(group_payload)
            coverage_groups[self._safe_str(group.get("group_id"))] = group
        source_backed = [
            self._capability_item(
                "client_profile",
                "lotus-core",
                "present",
                "Client, advisor, booking center, portfolio profile, and mandate profile.",
            ),
            self._capability_item(
                "portfolio_summary",
                "lotus-core",
                self._safe_str(coverage_groups.get("portfolio_value", {}).get("status")),
                "Portfolio market value, cash, invested value, and review date.",
            ),
            self._capability_item(
                "asset_allocation",
                "lotus-core",
                self._safe_str(coverage_groups.get("allocation", {}).get("status")),
                "Asset-class and supported allocation views.",
            ),
            self._capability_item(
                "holdings_pnl_cost_basis",
                "lotus-core",
                self._safe_str(
                    coverage_groups.get("position_pnl_and_cost_basis", {}).get("status")
                ),
                "Position market value, cost basis, unrealized P&L, and instrument reference data.",
            ),
            self._capability_item(
                "transaction_activity",
                "lotus-core",
                self._safe_str(coverage_groups.get("transactions", {}).get("status")),
                "Transaction rows, cash flows, income, fees, and taxes for the review window.",
            ),
            self._capability_item(
                "transaction_realized_gain_loss",
                "lotus-core",
                self._safe_str(
                    coverage_groups.get("transaction_realized_gain_loss", {}).get("status")
                ),
                "Transaction-level realized gain/loss for disposal and realized-P&L rows.",
            ),
            self._capability_item(
                "performance_returns",
                "lotus-performance",
                self._safe_str(coverage_groups.get("performance", {}).get("status")),
                "Time-weighted return periods used by the performance review.",
            ),
            self._capability_item(
                "performance_contribution",
                "lotus-performance",
                self._safe_str(coverage_groups.get("performance_contribution", {}).get("status")),
                "YTD position, asset-class, and sector contribution.",
            ),
            self._capability_item(
                "risk_metrics",
                "lotus-risk",
                self._safe_str(coverage_groups.get("risk", {}).get("status")),
                "Volatility, drawdown, VaR, expected shortfall, and risk-adjusted return.",
            ),
        ]
        risk = self._as_dict(response.get("riskAnalytics"))
        risk_metadata = self._as_dict(risk.get("metadata"))
        risk_free_context = self._as_dict(risk_metadata.get("risk_free_context"))
        if risk_free_context.get("requested"):
            risk_free_status = (
                "present"
                if risk_free_context.get("reason") == "ANNUAL_RATE_APPLIED"
                else "not_sourced"
            )
            source_backed.append(
                self._capability_item(
                    "source_backed_risk_free_rate",
                    "lotus-core / lotus-risk",
                    risk_free_status,
                    (
                        "Risk-free rate treatment for Sharpe sourced through lotus-risk stateful "
                        "analytics and lotus-core mastered risk-free series when available."
                    ),
                )
            )
        benchmark_status = self._safe_str(
            coverage_groups.get("benchmark_comparison", {}).get("status")
        )
        source_backed.append(
            self._capability_item(
                "benchmark_return_series",
                "lotus-performance / lotus-risk",
                benchmark_status,
                (
                    "Benchmark-relative excess return, tracking error, information ratio, beta, "
                    "and benchmark-relative risk sourced through lotus-performance and lotus-risk."
                ),
            )
        )
        upstream_gaps = [
            self._capability_gap(
                "targets_guidelines_suitability",
                "lotus-advise / lotus-manage",
                "not_sourced",
                (
                    "Target allocation, mandate guideline checks, liquidity needs, product "
                    "restrictions, suitability posture, and review approval state are not "
                    "authoritative inputs to lotus-report yet."
                ),
                ["client_profile", "asset_allocation", "holdings_appendix"],
            ),
            self._capability_gap(
                "tax_lot_jurisdiction_tax_treatment",
                "lotus-core / tax domain",
                "not_sourced",
                (
                    "Open tax-lot reporting, lot-level realized gain/loss attribution, and "
                    "jurisdiction-specific tax treatment are not exposed as governed upstream "
                    "report inputs."
                ),
                ["holdings_appendix", "transactions_appendix"],
            ),
        ]
        if benchmark_status != "present":
            upstream_gaps.insert(
                0,
                self._capability_gap(
                    "benchmark_return_series",
                    "lotus-performance / lotus-risk",
                    benchmark_status,
                    (
                        "Benchmark-relative excess return, tracking error, information ratio, "
                        "beta, and benchmark-relative risk cannot be certified until benchmark "
                        "return series are source-backed."
                    ),
                    ["performance_review", "risk_review"],
                ),
            )
        for note in self._as_list(self._as_dict(risk.get("supportability")).get("notes")):
            note_payload = self._as_dict(note)
            if note_payload.get("code") == "missing_risk_free_rate":
                upstream_gaps.append(
                    self._capability_gap(
                        "source_backed_risk_free_rate",
                        "lotus-risk / market-data domain",
                        "not_sourced",
                        self._safe_str(note_payload.get("message")),
                        ["risk_review"],
                    )
                )
                break
        report_side_findings = self._report_side_findings(response)
        return {
            "status": "action_required" if upstream_gaps or report_side_findings else "complete",
            "source_backed_capabilities": source_backed,
            "upstream_gaps": upstream_gaps,
            "report_side_findings": report_side_findings,
        }

    def _capability_item(
        self, capability_id: str, source_service: str, status_value: str, description: str
    ) -> dict[str, object]:
        return {
            "capability_id": capability_id,
            "source_service": source_service,
            "status": status_value or "missing",
            "description": description,
        }

    def _capability_gap(
        self,
        capability_id: str,
        owning_service: str,
        status_value: str,
        impact: str,
        source_section_ids: list[str],
    ) -> dict[str, object]:
        return {
            "capability_id": capability_id,
            "owning_service": owning_service,
            "status": status_value or "not_sourced",
            "impact": impact,
            "source_section_ids": source_section_ids,
        }

    def _report_side_findings(self, response: dict[str, object]) -> list[dict[str, object]]:
        findings: list[dict[str, object]] = []
        holdings = self._as_dict(self._as_dict(response.get("keyFigures")).get("holdings"))
        holdings_section = self._as_dict(response.get("holdings"))
        holdings_supportability = self._as_dict(holdings_section.get("supportability"))
        transactions = self._as_dict(response.get("transactions"))
        transaction_supportability = self._as_dict(transactions.get("supportability"))
        if holdings.get("unrealized_pnl_coverage") not in {"present", None}:
            findings.append(
                {
                    "finding_id": "incomplete_position_unrealized_pnl",
                    "severity": "gap",
                    "message": "Some position rows are missing unrealized P&L or cost basis.",
                    "source_section_ids": ["holdings_appendix"],
                }
            )
        if transaction_supportability.get("status") == "partial":
            findings.append(
                {
                    "finding_id": "transaction_window_truncated",
                    "severity": "watch",
                    "message": self._supportability_message(
                        transaction_supportability,
                        "Transactions Appendix",
                    ),
                    "source_section_ids": [
                        "income_cash_activity",
                        "transactions_appendix",
                    ],
                }
            )
        if holdings_supportability.get("status") == "partial":
            findings.append(
                {
                    "finding_id": "holdings_source_quality_partial",
                    "severity": "watch",
                    "message": self._supportability_message(
                        holdings_supportability,
                        "Holdings Appendix",
                    ),
                    "source_section_ids": ["holdings_appendix"],
                }
            )
        if not self._as_list(response.get("client_sections")):
            findings.append(
                {
                    "finding_id": "client_sections_missing",
                    "severity": "blocking",
                    "message": "Client-ready section envelope is missing from the report response.",
                    "source_section_ids": [],
                }
            )
        return findings

    def _client_profile_coverage(self, response: dict[str, object]) -> str:
        profile = self._as_dict(response.get("clientProfile"))
        profile_status = self._safe_str(profile.get("status"))
        if profile_status in {"present", "partial", "unavailable"}:
            return profile_status
        return "unavailable"

    def _figure_group(
        self, group_id: str, key_figures: dict[str, object], *, required: bool
    ) -> dict[str, object]:
        group = self._as_dict(key_figures.get(group_id))
        supportability_status = self._safe_str(group.get("supportability_status"))
        populated = any(
            key != "supportability_status" and value not in (None, "", {}, [])
            for key, value in group.items()
        )
        if supportability_status in {"partial", "unavailable"}:
            status_value = supportability_status
        else:
            status_value = "present" if populated else "missing"
        return {
            "group_id": group_id,
            "status": status_value,
            "required": required,
        }

    def _ai_assistance_coverage(self, response: dict[str, object]) -> str:
        client_profile = self._as_dict(response.get("clientProfile"))
        if client_profile.get("status") == "present" and response.get("keyFigures"):
            return "guarded_ready"
        if response.get("keyFigures"):
            return "partial"
        return "not_ready"

    def _benchmark_comparison_coverage(self, response: dict[str, object]) -> str:
        performance = self._as_dict(response.get("performance"))
        benchmark = self._as_dict(performance.get("benchmark"))
        if not benchmark.get("benchmark_code"):
            return "not_requested"
        if benchmark.get("comparison_status") == "available":
            return "present"
        return "partial"

    def _position_pnl_coverage(self, response: dict[str, object]) -> str:
        holdings = self._as_dict(response.get("holdings"))
        rows = self._holding_rows(holdings)
        populated = [
            row
            for row in rows
            if self._optional_number_raw(row.get("cost_basis_reporting_currency")) is not None
            and self._optional_number_raw(row.get("unrealized_pnl_reporting_currency")) is not None
        ]
        return self._coverage_status(len(populated), len(rows))

    def _contribution_coverage(self, response: dict[str, object]) -> str:
        contribution = self._as_dict(self._as_dict(response.get("performance")).get("contribution"))
        if contribution.get("status") == "present":
            return "present"
        if response.get("performance") is None:
            return "not_sourced"
        return "unavailable"

    def _transaction_realized_pnl_coverage(self, response: dict[str, object]) -> str:
        transactions = self._as_dict(response.get("transactions"))
        key_figures = self._as_dict(self._as_dict(response.get("keyFigures")).get("transactions"))
        if key_figures.get("supportability_status") == "partial":
            return "partial"
        if key_figures.get("realized_pnl_status") == "present":
            return "present"
        if not transactions:
            return "not_requested"
        if self._to_int(transactions.get("transactionCount")) == 0:
            return "not_applicable"
        return "not_applicable"

    def _instrument_reference_coverage(self, response: dict[str, object]) -> str:
        holdings = self._as_dict(response.get("holdings"))
        rows = self._holding_rows(holdings)
        populated = [
            row
            for row in rows
            if self._safe_str(row.get("isin"))
            and self._safe_str(row.get("product_type"))
            and self._safe_str(row.get("liquidity_tier"))
        ]
        return self._coverage_status(len(populated), len(rows))

    def _period_return(self, summary: dict[str, object], period: str, key: str) -> object | None:
        return self._as_dict(summary.get(period)).get(key)

    def _expected_shortfall(self, risk: dict[str, object], period: str) -> object | None:
        results = self._as_dict(risk.get("results"))
        period_result = self._as_dict(results.get(period))
        metrics = self._as_dict(period_result.get("metrics"))
        var_metric = self._as_dict(metrics.get("VAR"))
        details = self._as_dict(var_metric.get("details"))
        return details.get("expected_shortfall")

    def _risk_benchmark_status(self, risk: dict[str, object]) -> str:
        supportability = self._as_dict(risk.get("supportability"))
        for note in self._as_list(supportability.get("notes")):
            note_payload = self._as_dict(note)
            if note_payload.get("code") == "missing_benchmark":
                return "unavailable"
        return "available"

    def _holding_rows(self, holdings: dict[str, object]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for asset_class, asset_rows in self._as_dict(holdings.get("holdingsByAssetClass")).items():
            for row_payload in self._as_list(asset_rows):
                row = self._as_dict(row_payload).copy()
                row["asset_class"] = self._safe_str(asset_class)
                rows.append(row)
        return rows

    def _top_bucket(self, buckets: list[object]) -> dict[str, object] | None:
        rows = [self._as_dict(bucket) for bucket in buckets]
        rows = [row for row in rows if row]
        if not rows:
            return None
        return max(rows, key=lambda row: self._to_float(row.get("weight")))

    def _bucket_key_figure(self, bucket: dict[str, object] | None) -> dict[str, object] | None:
        if bucket is None:
            return None
        return {
            "name": bucket.get("group"),
            "weight_pct": self._to_float(bucket.get("weight")) * 100,
            "market_value_reporting_currency": self._to_float(bucket.get("market_value")),
            "position_count": self._to_int(bucket.get("position_count")),
        }

    def _holding_key_figure(self, row: dict[str, object]) -> dict[str, object]:
        return {
            "security_id": row.get("security_id"),
            "instrument_name": row.get("instrument_name"),
            "asset_class": row.get("asset_class"),
            "market_value_reporting_currency": self._to_float(
                row.get("market_value_reporting_currency")
            ),
            "weight_pct": self._to_float(row.get("weight")) * 100,
            "currency": row.get("currency"),
            "unrealized_pnl_reporting_currency": row.get("unrealized_pnl_reporting_currency"),
            "unrealized_pnl_pct": row.get("unrealized_pnl_pct"),
            "ytd_contribution_pct": row.get("ytd_contribution_pct"),
        }

    def _contribution_extreme(
        self, rows: list[object], *, largest: bool
    ) -> dict[str, object] | None:
        mapped_rows = [self._as_dict(row) for row in rows]
        mapped_rows = [
            row
            for row in mapped_rows
            if self._optional_number_raw(row.get("total_contribution_pct")) is not None
        ]
        if not mapped_rows:
            return None
        selected = (
            max(
                mapped_rows,
                key=lambda row: self._to_float(row.get("total_contribution_pct")),
            )
            if largest
            else min(
                mapped_rows,
                key=lambda row: self._to_float(row.get("total_contribution_pct")),
            )
        )
        return {
            "security_id": selected.get("security_id"),
            "position_id": selected.get("position_id"),
            "total_contribution_pct": selected.get("total_contribution_pct"),
            "average_weight_pct": selected.get("average_weight_pct"),
            "total_return_pct": selected.get("total_return_pct"),
        }

    def _safe_pct(self, numerator: float, denominator: float) -> float | None:
        if denominator == 0:
            return None
        return (numerator / denominator) * 100

    def _optional_number_raw(self, raw: object) -> float | None:
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            parsed = float(raw)
            return parsed
        if isinstance(raw, str):
            try:
                parsed = float(raw)
                return parsed
            except ValueError:
                return None
        return None

    def _performance_benchmark_context(
        self,
        request_payload: dict[str, object],
        *,
        available: bool = False,
        resolved_benchmark_code: str | None = None,
        return_source: str | None = None,
    ) -> dict[str, object]:
        requested_benchmark_code = self._optional_string(request_payload, *BENCHMARK_CODE_KEYS)
        benchmark_code = self._normalized_benchmark_code(requested_benchmark_code)
        if available:
            return {
                "benchmark_code": resolved_benchmark_code or benchmark_code,
                "requested_benchmark_code": requested_benchmark_code,
                "comparison_status": "available",
                "return_source": return_source,
                "reason_code": None,
            }
        return {
            "benchmark_code": benchmark_code,
            "comparison_status": "unavailable" if benchmark_code else "not_requested",
            "reason_code": "benchmark_return_series_not_sourced" if benchmark_code else None,
        }

    def _performance_supportability(
        self,
        request_payload: dict[str, object],
        *,
        benchmark_available: bool = False,
    ) -> dict[str, object]:
        if not self._optional_string(request_payload, *BENCHMARK_CODE_KEYS):
            return {"status": "ready", "notes": []}
        if benchmark_available:
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

    def _map_review_transaction_row(self, row: dict[str, object]) -> dict[str, object]:
        transaction_id = self._safe_str(row.get("transaction_id"))
        transaction_type = self._safe_str(row.get("transaction_type")).upper()
        cash_leg = transaction_id.startswith("TXN-CASH-")
        category = self._transaction_review_category(
            transaction_type=transaction_type,
            cash_leg=cash_leg,
            asset_class=self._safe_str(row.get("asset_class")),
        )
        gross_amount = self._to_float(row.get("gross_transaction_amount_reporting_currency"))
        interest_amount = self._to_float(row.get("net_interest_amount_reporting_currency"))
        tax_amount = self._to_float(row.get("withholding_tax_amount_reporting_currency"))
        amount = interest_amount if transaction_type in {"DIVIDEND", "INTEREST"} else gross_amount
        return {
            "transaction_id": transaction_id,
            "transaction_date": self._safe_str(row.get("transaction_date")),
            "settlement_date": self._safe_str(row.get("settlement_date")) or None,
            "transaction_type": transaction_type,
            "instrument_id": self._safe_str(row.get("instrument_id")) or None,
            "security_id": self._safe_str(row.get("security_id")) or None,
            "transaction_category": category,
            "display_label": self._transaction_display_label(transaction_type, cash_leg),
            "cash_leg": cash_leg,
            "asset_class": self._safe_str(row.get("asset_class")) or None,
            "amount_reporting_currency": amount,
            "gross_transaction_amount_reporting_currency": gross_amount,
            "realized_pnl_reporting_currency": self._optional_number_raw(
                self._realized_pnl_reporting_amount(row)
            ),
            "realized_pnl_local": self._optional_number_raw(
                row.get("realized_gain_loss_local") or row.get("realized_total_pnl_local")
            ),
            "net_interest_amount_reporting_currency": interest_amount,
            "withholding_tax_amount_reporting_currency": tax_amount,
            "income_or_tax_reporting_currency": interest_amount - tax_amount,
            "linked_costs": self._transaction_linked_costs(row),
            "linked_cashflow": self._transaction_linked_cashflow(row),
            "source_system": self._safe_str(row.get("source_system")) or None,
            "source_record_id": self._safe_str(row.get("source_record_id")) or None,
            "source_event_id": self._safe_str(row.get("source_event_id")) or None,
            "linked_transaction_group_id": self._safe_str(row.get("linked_transaction_group_id"))
            or None,
            "correction_of_transaction_id": self._safe_str(row.get("correction_of_transaction_id"))
            or None,
            "reversal_of_transaction_id": self._safe_str(row.get("reversal_of_transaction_id"))
            or None,
        }

    def _transaction_linked_costs(self, row: dict[str, object]) -> list[dict[str, object]]:
        costs: list[dict[str, object]] = []
        for cost_payload in self._as_list(row.get("costs")):
            cost = self._as_dict(cost_payload)
            if cost:
                costs.append(dict(cost))
        return costs

    def _transaction_linked_cashflow(self, row: dict[str, object]) -> dict[str, object] | None:
        cashflow = self._as_dict(row.get("cashflow"))
        return dict(cashflow) if cashflow else None

    def _transaction_review_category(
        self, *, transaction_type: str, cash_leg: bool, asset_class: str
    ) -> str:
        if cash_leg:
            return "Cash Ledger"
        if transaction_type in {"DIVIDEND", "INTEREST"}:
            return "Income"
        if transaction_type in {"DEPOSIT", "TRANSFER_IN", "WITHDRAWAL", "TRANSFER_OUT"}:
            return "Cash Flow"
        if transaction_type in {"BUY", "SELL"}:
            return "Trading"
        if transaction_type in {"FEE", "TAX"}:
            return "Fees And Taxes"
        return asset_class or "Other"

    def _transaction_display_label(self, transaction_type: str, cash_leg: bool) -> str:
        if cash_leg:
            return f"Cash ledger leg for {transaction_type.title()}"
        return transaction_type.replace("_", " ").title() or "Transaction"

    async def _build_risk_attribution(
        self,
        portfolio_id: str,
        as_of_date: str,
        request_payload: dict[str, object],
    ) -> dict[str, object]:
        """One upstream call for risk attribution (#254, contract locked with
        Render 2026-09-04).

        Report forwards the source's decomposition facts verbatim: the
        reconciliation triple, contributors in source order, quality flags,
        the benchmark context, and the source's unit statement. Nothing is
        ranked, rescaled, re-reconciled, or allocated here. TOTAL_RISK x
        VOLATILITY always; ACTIVE_RISK x TRACKING_ERROR only when the report
        states a benchmark - active-risk availability is the source's fact.
        """

        benchmark_code = self._normalized_benchmark_code(
            self._optional_string(request_payload, *BENCHMARK_CODE_KEYS)
        )
        attribution_types = ["TOTAL_RISK"]
        metrics = ["VOLATILITY"]
        if benchmark_code:
            attribution_types.append("ACTIVE_RISK")
            metrics.append("TRACKING_ERROR")
        attribution_payload: dict[str, object] = {
            "input_mode": "stateful",
            "stateful_input": {
                "portfolio_id": portfolio_id,
                "as_of_date": as_of_date,
                "reporting_currency": self._optional_string(
                    request_payload, *REPORTING_CURRENCY_KEYS
                ),
                "client_id": self._optional_string(request_payload, *CLIENT_ID_KEYS),
                "benchmark_id": benchmark_code,
                "net_or_gross": "NET",
                "periods": [{"type": "YTD", "name": "YTD"}],
                "attribution_options": {
                    "attribution_types": attribution_types,
                    "metrics": metrics,
                    "grouping_dimensions": ["SECTOR"],
                },
            },
        }
        try:
            status_code, response_payload = await self._risk_client.historical_attribution(
                attribution_payload
            )
        except Exception:
            status_code, response_payload = 0, {}
        request_block = {
            "attribution_types": attribution_types,
            "metrics": metrics,
            "grouping_dimension": "SECTOR",
        }
        if status_code >= HTTP_BAD_REQUEST or status_code == 0:
            return {
                "source": {
                    "service": "lotus-risk",
                    "endpoint": "/analytics/risk/historical-attribution",
                },
                "request": request_block,
                "supportability": {
                    "status": "unavailable",
                    "notes": [
                        {
                            "code": "risk_attribution_upstream_failure",
                            "severity": "blocking",
                            "message": (
                                "Risk attribution is unavailable because lotus-risk could "
                                "not calculate the decomposition."
                            ),
                        }
                    ],
                },
                "results": {},
                "metadata": {},
            }
        return {
            "source": {
                "service": "lotus-risk",
                "endpoint": "/analytics/risk/historical-attribution",
            },
            "request": request_block,
            "supportability": {"status": "ready", "notes": []},
            "results": self._as_dict(response_payload.get("results")),
            "metadata": self._as_dict(response_payload.get("metadata")),
        }

    async def _build_risk_trend(
        self,
        portfolio_id: str,
        as_of_date: str,
        request_payload: dict[str, object],
    ) -> dict[str, object]:
        """One upstream call for the risk-trend series (#255, agreed contract
        on report#255 + render#160).

        Report forwards the source's series and coverage facts verbatim: no
        smoothing, no resampling, no gap-filling, no derived verdicts. A
        trend statement appears ONLY if lotus-risk states one (it does not
        today, so none appears). Benchmark-dependent metrics are requested
        only when the report states a benchmark, and their availability is
        the source's benchmark_context fact - never inferred here.
        """

        benchmark_code = self._normalized_benchmark_code(
            self._optional_string(request_payload, *BENCHMARK_CODE_KEYS)
        )
        metrics = ["ROLLING_VOLATILITY"]
        if benchmark_code:
            metrics.extend(["ROLLING_BETA", "ROLLING_TRACKING_ERROR"])
        rolling_payload: dict[str, object] = {
            "input_mode": "stateful",
            "stateful_input": {
                "portfolio_id": portfolio_id,
                "as_of_date": as_of_date,
                "reporting_currency": self._optional_string(
                    request_payload, *REPORTING_CURRENCY_KEYS
                ),
                "client_id": self._optional_string(request_payload, *CLIENT_ID_KEYS),
                "benchmark_id": benchmark_code,
                "net_or_gross": "NET",
                "periods": [{"type": "YTD", "name": "YTD"}],
                "rolling_options": {
                    "window_lengths": [ROLLING_TREND_WINDOW_OBSERVATIONS],
                    "metrics": metrics,
                    "include_time_series": True,
                },
            },
        }
        try:
            status_code, response_payload = await self._risk_client.rolling_metrics(rolling_payload)
        except Exception:
            status_code, response_payload = 0, {}
        if status_code >= HTTP_BAD_REQUEST or status_code == 0:
            return {
                "source": {
                    "service": "lotus-risk",
                    "endpoint": "/analytics/risk/rolling-metrics",
                },
                "request": {
                    "window_observations": ROLLING_TREND_WINDOW_OBSERVATIONS,
                    "metrics": metrics,
                    "frequency": "daily",
                },
                "supportability": {
                    "status": "unavailable",
                    "notes": [
                        {
                            "code": "risk_trend_upstream_failure",
                            "severity": "blocking",
                            "message": (
                                "Risk trend is unavailable because lotus-risk could not "
                                "calculate rolling metrics."
                            ),
                        }
                    ],
                },
                "results": {},
                "metadata": {},
            }
        return {
            "source": {
                "service": "lotus-risk",
                "endpoint": "/analytics/risk/rolling-metrics",
            },
            "request": {
                "window_observations": ROLLING_TREND_WINDOW_OBSERVATIONS,
                "metrics": metrics,
                "frequency": "daily",
            },
            "supportability": {"status": "ready", "notes": []},
            "results": self._as_dict(response_payload.get("results")),
            "metadata": self._as_dict(response_payload.get("metadata")),
        }

    def _build_risk_stateful_input(
        self,
        *,
        portfolio_id: str,
        as_of_date: str,
        request_payload: dict[str, object],
    ) -> dict[str, object]:
        benchmark_code = self._normalized_benchmark_code(
            self._optional_string(request_payload, *BENCHMARK_CODE_KEYS)
        )
        metrics = self._requested_risk_metrics(request_payload)
        return {
            "portfolio_id": portfolio_id,
            "as_of_date": as_of_date,
            "reporting_currency": self._optional_string(request_payload, *REPORTING_CURRENCY_KEYS),
            "client_id": self._optional_string(request_payload, *CLIENT_ID_KEYS),
            "benchmark_id": benchmark_code,
            "net_or_gross": "NET",
            "periods": [
                {"type": "YTD", "name": "YTD"},
                {"type": "1Y", "name": "1Y"},
            ],
            "metrics": metrics,
            "options": {
                "frequency": "DAILY",
                "var": {
                    "method": "HISTORICAL",
                    "confidence": 0.95,
                    "horizon_days": 1,
                    "include_expected_shortfall": True,
                },
            },
        }

    def _normalized_benchmark_code(self, benchmark_code: str | None) -> str | None:
        if benchmark_code is None:
            return None
        return BENCHMARK_CODE_ALIASES.get(benchmark_code, benchmark_code)

    def _requested_risk_metrics(self, request_payload: dict[str, object]) -> list[str]:
        metrics = list(RISK_METRICS)
        if self._optional_string(request_payload, *BENCHMARK_CODE_KEYS):
            metrics.extend(BENCHMARK_RISK_METRICS)
        return metrics

    def _position_market_value(self, row: dict[str, object]) -> float:  # monetary-float-allow
        if row.get("market_value_reporting_currency") is not None:
            return self._to_float(row.get("market_value_reporting_currency"))
        valuation = self._as_dict(row.get("valuation"))
        return self._to_float(valuation.get("market_value"))

    def _position_number(
        self,
        row: dict[str, object],
        row_keys: tuple[str, ...],
        valuation_keys: tuple[str, ...] = (),
    ) -> float | None:
        for key in row_keys:
            parsed = self._optional_number_raw(row.get(key))
            if parsed is not None:
                return parsed
        valuation = self._as_dict(row.get("valuation"))
        for key in valuation_keys:
            parsed = self._optional_number_raw(valuation.get(key))
            if parsed is not None:
                return parsed
        return None

    def _position_unrealized_pnl_pct(self, row: dict[str, object]) -> object | None:
        explicit = self._position_number(
            row,
            ("unrealized_pnl_pct", "unrealized_gain_loss_pct"),
            ("unrealized_pnl_pct", "unrealized_gain_loss_pct"),
        )
        if explicit is not None:
            return explicit
        pnl = self._position_number(
            row,
            ("unrealized_pnl_reporting_currency", "unrealized_gain_loss", "unrealized_pnl"),
            ("unrealized_gain_loss", "unrealized_pnl"),
        )
        cost_basis = self._position_number(
            row,
            ("cost_basis_reporting_currency", "cost_basis"),
        )
        if pnl is None or cost_basis is None or cost_basis == 0:
            return None
        return (pnl / abs(cost_basis)) * 100

    @staticmethod
    def _workspace_summary_ready(status_code: int, payload: dict[str, object]) -> bool:
        return status_code < HTTP_BAD_REQUEST and "results_by_period" in payload

    def _section_items(self, section_id: str, section_payload: object) -> list[dict[str, object]]:
        section = self._as_dict(section_payload)
        if section_id == "client_profile":
            return self._client_profile_items(section)
        if section_id == "executive_summary":
            return self._overview_items(section)
        if section_id == "asset_allocation":
            return self._allocation_items(section)
        if section_id == "performance_review":
            return self._performance_items(section)
        if section_id == "risk_review":
            return self._risk_items(section)
        if section_id == "income_cash_activity":
            return self._income_activity_items(section)
        if section_id == "holdings_appendix":
            return self._holding_items(section)
        if section_id == "transactions_appendix":
            return self._transaction_items(section)
        return [{"item_type": "section_payload", "payload": section}]

    def _client_profile_items(self, profile: dict[str, object]) -> list[dict[str, object]]:
        identity = self._as_dict(profile.get("identity"))
        portfolio_profile = self._as_dict(profile.get("portfolio_profile"))
        mandate_profile = self._as_dict(profile.get("mandate_profile"))
        items: list[dict[str, object]] = [
            {
                "item_type": "client_identity",
                "client_id": identity.get("client_id"),
                "advisor_id": identity.get("advisor_id"),
                "booking_center_code": identity.get("booking_center_code"),
                "profile_status": profile.get("status"),
            },
            {
                "item_type": "portfolio_profile",
                "portfolio_id": portfolio_profile.get("portfolio_id"),
                "base_currency": portfolio_profile.get("base_currency"),
                "open_date": portfolio_profile.get("open_date"),
                "status": portfolio_profile.get("status"),
            },
            {
                "item_type": "mandate_profile",
                "portfolio_type": mandate_profile.get("portfolio_type"),
                "objective": mandate_profile.get("objective"),
                "risk_exposure": mandate_profile.get("risk_exposure"),
                "investment_time_horizon": mandate_profile.get("investment_time_horizon"),
                "is_leverage_allowed": mandate_profile.get("is_leverage_allowed"),
                "cost_basis_method": mandate_profile.get("cost_basis_method"),
            },
        ]
        missing_fields = [
            self._safe_str(field) for field in self._as_list(profile.get("missing_fields"))
        ]
        if missing_fields:
            items.append({"item_type": "missing_profile_fields", "fields": missing_fields})
        return items

    def _overview_items(self, overview: dict[str, object]) -> list[dict[str, object]]:
        currency = self._safe_str(overview.get("currency"))
        metrics = (
            ("total_market_value", "Total market value"),
            ("total_cash", "Total cash"),
            ("invested_market_value", "Invested market value"),
        )
        return [
            {
                "item_type": "measure",
                "metric": metric,
                "label": label,
                "value": self._to_float(overview.get(metric)),
                "unit": "money",
                "currency": currency,
            }
            for metric, label in metrics
            if metric in overview
        ]

    def _allocation_items(self, allocation: dict[str, object]) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for view_key, buckets in allocation.items():
            if not isinstance(view_key, str):
                continue
            for rank, bucket in enumerate(self._as_list(buckets), start=1):
                row = self._as_dict(bucket)
                items.append(
                    {
                        "item_type": "allocation_bucket",
                        "view": view_key,
                        "rank": rank,
                        "group": row.get("group"),
                        "weight": self._to_float(row.get("weight")),
                        "market_value": self._to_float(row.get("market_value")),
                        "position_count": self._to_int(row.get("position_count")),
                    }
                )
        return items

    def _performance_items(self, performance: dict[str, object]) -> list[dict[str, object]]:
        summary = self._as_dict(performance.get("summary"))
        benchmark = self._as_dict(performance.get("benchmark"))
        items = [
            {
                "item_type": "performance_period",
                "period": period,
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
                "net_cumulative_return": row.get("net_cumulative_return"),
                "net_annualized_return": row.get("net_annualized_return"),
                "gross_cumulative_return": row.get("gross_cumulative_return"),
                "gross_annualized_return": row.get("gross_annualized_return"),
                "benchmark_cumulative_return": row.get("benchmark_cumulative_return"),
                "benchmark_relative_return": row.get("benchmark_relative_return"),
                "annualized_return_supported": row.get("annualized_return_supported"),
                "benchmark_code": benchmark.get("benchmark_code"),
                "benchmark_comparison_status": benchmark.get("comparison_status"),
            }
            for period, period_payload in summary.items()
            for row in [self._as_dict(period_payload)]
        ]
        contribution = self._as_dict(performance.get("contribution"))
        if contribution.get("status") == "present":
            items.append(
                {
                    "item_type": "contribution_summary",
                    "period": contribution.get("period"),
                    "total_portfolio_return_pct": contribution.get("total_portfolio_return_pct"),
                    "total_contribution_pct": contribution.get("total_contribution_pct"),
                }
            )
            for row_payload in self._as_list(contribution.get("top_position_contributors")):
                row = self._as_dict(row_payload)
                items.append({"item_type": "position_contribution", **row})
            for level_payload in self._as_list(contribution.get("hierarchy")):
                level = self._as_dict(level_payload)
                for row_payload in self._as_list(level.get("rows")):
                    row = self._as_dict(row_payload)
                    items.append(
                        {
                            "item_type": "hierarchy_contribution",
                            "level": level.get("level"),
                            "dimension": level.get("name"),
                            **row,
                        }
                    )
        return items

    def _risk_items(self, risk_analytics: dict[str, object]) -> list[dict[str, object]]:
        summary = self._as_dict(risk_analytics.get("summary"))
        return [
            {
                "item_type": "risk_period",
                "period": period,
                "volatility": row.get("volatility"),
                "risk_adjusted_return": row.get("risk_adjusted_return"),
                "drawdown": row.get("drawdown"),
                "value_at_risk": row.get("value_at_risk"),
                "beta": row.get("beta"),
                "tracking_error": row.get("tracking_error"),
                "information_ratio": row.get("information_ratio"),
            }
            for period, period_payload in summary.items()
            for row in [self._as_dict(period_payload)]
        ]

    def _income_activity_items(
        self, income_and_activity: dict[str, object]
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        income_summary = self._as_dict(income_and_activity.get("incomeSummary"))
        if income_summary:
            items.append({"item_type": "income_summary", **income_summary})
        activity_summary = self._as_dict(income_and_activity.get("activitySummary"))
        for key, value in activity_summary.items():
            if not key.startswith("total_"):
                continue
            bucket = key.removeprefix("total_")
            items.append(
                {
                    "item_type": "activity_flow",
                    "bucket": bucket.upper(),
                    "amount_reporting_currency": self._to_float(value),
                    "transaction_count": self._to_int(
                        activity_summary.get(f"{bucket}_transaction_count")
                    ),
                }
            )
        realized_pnl_summary = self._as_dict(income_and_activity.get("realizedPnlSummary"))
        if realized_pnl_summary:
            items.append({"item_type": "realized_pnl_summary", **realized_pnl_summary})
        return items

    def _holding_items(self, holdings: dict[str, object]) -> list[dict[str, object]]:
        items = [
            {
                "item_type": "holdings_summary",
                "position_count": self._to_int(holdings.get("positionCount")),
                "source_product": self._as_dict(holdings.get("sourceProduct")),
                "supportability": self._as_dict(holdings.get("supportability")),
            }
        ]
        holdings_by_asset_class = self._as_dict(holdings.get("holdingsByAssetClass"))
        for asset_class, rows in holdings_by_asset_class.items():
            for rank, row_payload in enumerate(self._as_list(rows), start=1):
                row = self._as_dict(row_payload)
                items.append(
                    {
                        "item_type": "holding",
                        "asset_class": asset_class,
                        "rank": rank,
                        "security_id": row.get("security_id"),
                        "instrument_name": row.get("instrument_name"),
                        "isin": row.get("isin"),
                        "position_id": row.get("position_id"),
                        "quantity": row.get("quantity"),
                        "position_date": row.get("position_date"),
                        "position_state_status": row.get("position_state_status"),
                        "position_state_epoch": row.get("position_state_epoch"),
                        "row_evidence_timestamp": row.get("row_evidence_timestamp"),
                        "row_snapshot_id": row.get("row_snapshot_id"),
                        "source_system": row.get("source_system"),
                        "source_record_id": row.get("source_record_id"),
                        "source_transaction_id": row.get("source_transaction_id"),
                        "lot_status": row.get("lot_status"),
                        "product_type": row.get("product_type"),
                        "sector": row.get("sector"),
                        "country_of_risk": row.get("country_of_risk"),
                        "rating": row.get("rating"),
                        "liquidity_tier": row.get("liquidity_tier"),
                        "held_since_date": row.get("held_since_date"),
                        "market_price": row.get("market_price"),
                        "cost_basis_reporting_currency": row.get("cost_basis_reporting_currency"),
                        "cost_basis_local": row.get("cost_basis_local"),
                        "market_value_reporting_currency": row.get(
                            "market_value_reporting_currency"
                        ),
                        "market_value_local": row.get("market_value_local"),
                        "unrealized_pnl_reporting_currency": row.get(
                            "unrealized_pnl_reporting_currency"
                        ),
                        "unrealized_pnl_local": row.get("unrealized_pnl_local"),
                        "unrealized_pnl_pct": row.get("unrealized_pnl_pct"),
                        "ytd_contribution_pct": row.get("ytd_contribution_pct"),
                        "ytd_average_weight_pct": row.get("ytd_average_weight_pct"),
                        "ytd_total_return_pct": row.get("ytd_total_return_pct"),
                        "weight": row.get("weight"),
                        "currency": row.get("currency"),
                    }
                )
        return items

    def _transaction_items(self, transactions: dict[str, object]) -> list[dict[str, object]]:
        items = [
            {
                "item_type": "transactions_summary",
                "transaction_count": self._to_int(transactions.get("transactionCount")),
                "source_product": self._as_dict(transactions.get("sourceProduct")),
                "supportability": self._as_dict(transactions.get("supportability")),
            }
        ]
        transactions_by_category = self._as_dict(transactions.get("transactionsByCategory"))
        if not transactions_by_category:
            transactions_by_category = self._as_dict(transactions.get("transactionsByAssetClass"))
        for category, rows in transactions_by_category.items():
            for row_payload in self._as_list(rows):
                row = self._as_dict(row_payload)
                items.append(
                    {
                        "item_type": "transaction",
                        "category": category,
                        "asset_class": row.get("asset_class"),
                        "transaction_category": row.get("transaction_category"),
                        "display_label": row.get("display_label"),
                        "cash_leg": row.get("cash_leg"),
                        "transaction_id": row.get("transaction_id"),
                        "transaction_date": row.get("transaction_date"),
                        "settlement_date": row.get("settlement_date"),
                        "transaction_type": row.get("transaction_type"),
                        "instrument_id": row.get("instrument_id"),
                        "security_id": row.get("security_id"),
                        "amount_reporting_currency": row.get("amount_reporting_currency"),
                        "gross_transaction_amount_reporting_currency": row.get(
                            "gross_transaction_amount_reporting_currency"
                        ),
                        "realized_pnl_reporting_currency": row.get(
                            "realized_pnl_reporting_currency"
                        ),
                        "realized_pnl_local": row.get("realized_pnl_local"),
                        "net_interest_amount_reporting_currency": row.get(
                            "net_interest_amount_reporting_currency"
                        ),
                        "withholding_tax_amount_reporting_currency": row.get(
                            "withholding_tax_amount_reporting_currency"
                        ),
                        "linked_costs": row.get("linked_costs"),
                        "linked_cashflow": row.get("linked_cashflow"),
                        "source_system": row.get("source_system"),
                        "source_record_id": row.get("source_record_id"),
                        "source_event_id": row.get("source_event_id"),
                        "linked_transaction_group_id": row.get("linked_transaction_group_id"),
                        "correction_of_transaction_id": row.get("correction_of_transaction_id"),
                        "reversal_of_transaction_id": row.get("reversal_of_transaction_id"),
                    }
                )
        return items
