from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.clients.core_query_client import CoreQueryClient
from app.clients.performance_client import PerformanceClient
from app.clients.risk_client import RiskClient
from app.config import settings
from app.services.portfolio_review_advisor import build_advisor_sections

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
        response["reviewPeriod"] = self._review_period(as_of_date)
        response["reportingCurrency"] = self._reporting_currency(request_payload, summary)
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
                    periods=["1M", "3M", "YTD", "5Y", "SI"],
                )
            )
            if self._workspace_summary_ready(performance_status, performance_payload):
                workspace_summary_payload = performance_payload
            if self._workspace_summary_ready(performance_status, performance_payload):
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
        response["keyFigures"] = self._review_key_figures(response)
        response["reviewObservations"] = self._review_observations(response)
        response["reportCoverage"] = self._review_report_coverage(response)
        response["evidence"] = self._review_evidence(
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
            correlation_id=correlation_id,
            response=response,
        )
        response["client_sections"] = client_sections
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
            "reviewObservations": [],
            "disclosures": [],
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
            "input_mode": "stateless",
            "stateless_input": self._build_risk_stateless_input(
                as_of_date=as_of_date,
                request_payload=request_payload,
                portfolio_open_date=portfolio_open_date,
                returns=returns,
            ),
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
                    "severity": "warning",
                    "message": (
                        "Benchmark-relative risk posture is unavailable because benchmark "
                        "return series is not sourced for the risk calculation."
                    ),
                }
            )

        severities = {note.get("severity") for note in notes}
        if "blocking" in severities:
            status_value = "unavailable"
        elif "warning" in severities:
            status_value = "partial"
        else:
            status_value = "ready"
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

    def _review_evidence(
        self,
        *,
        portfolio_id: str,
        as_of_date: str,
        correlation_id: str | None,
        response: dict[str, object],
    ) -> dict[str, object]:
        source_refs = self._review_source_refs(portfolio_id=portfolio_id, response=response)
        source_services = sorted(
            {
                self._safe_str(source_ref.get("source_service"))
                for source_ref in source_refs
                if self._safe_str(source_ref.get("source_service"))
            }
        )
        lineage_bundle_id = f"lineage:lotus-report:portfolio-review:{portfolio_id}:{as_of_date}"
        evidence_bundle_id = f"evidence:lotus-report:portfolio-review:{portfolio_id}:{as_of_date}"
        readiness_status = self._as_dict(response.get("readiness")).get("status", "ready")
        completeness_status = "partial" if readiness_status == "partial" else "complete"
        data_quality_status = (
            "quality_warning" if readiness_status == "partial" else "quality_passed"
        )
        return {
            "product_id": "lotus-report:ClientReportEvidencePack:v1",
            "product_name": "ClientReportEvidencePack",
            "product_version": "v1",
            "lineage_bundle_id": lineage_bundle_id,
            "evidence_bundle_id": evidence_bundle_id,
            "evidence_access_class": "customer_consumable",
            "portfolio_id": portfolio_id,
            "as_of_date": as_of_date,
            "correlation_id": correlation_id,
            "source_services": source_services,
            "source_refs": source_refs,
            "trust_metadata": {
                "product_name": "ClientReportEvidencePack",
                "product_version": "v1",
                "tenant_id": "default",
                "generated_at": response.get("generated_at"),
                "as_of_date": as_of_date,
                "completeness_status": completeness_status,
                "reconciliation_status": "reconciled",
                "data_quality_status": data_quality_status,
                "source_batch_fingerprint": f"portfolio-review:{portfolio_id}:{as_of_date}",
                "lineage_bundle_id": lineage_bundle_id,
                "correlation_id": correlation_id,
            },
        }

    def _review_source_refs(
        self, *, portfolio_id: str, response: dict[str, object]
    ) -> list[dict[str, object]]:
        refs: list[dict[str, object]] = []
        self._append_source_ref(
            refs,
            response=response,
            response_key="overview",
            section_id="executive_summary",
            source_service="lotus-core",
            source_endpoint="/reporting/portfolio-summary/query",
            source_entity_id=portfolio_id,
        )
        self._append_source_ref(
            refs,
            response=response,
            response_key="allocation",
            section_id="asset_allocation",
            source_service="lotus-core",
            source_endpoint="/reporting/asset-allocation/query",
            source_entity_id=portfolio_id,
        )
        self._append_source_ref(
            refs,
            response=response,
            response_key="performance",
            section_id="performance_review",
            source_service="lotus-performance",
            source_endpoint="/performance/workspace-summary",
            source_entity_id=portfolio_id,
        )
        self._append_source_ref(
            refs,
            response=response,
            response_key="riskAnalytics",
            section_id="risk_review",
            source_service="lotus-risk",
            source_endpoint="/analytics/risk/calculate",
            source_entity_id=portfolio_id,
            input_services=["lotus-performance"],
        )
        self._append_source_ref(
            refs,
            response=response,
            response_key="incomeAndActivity",
            section_id="income_cash_activity",
            source_service="lotus-core",
            source_endpoint=f"/portfolios/{portfolio_id}/transactions",
            source_entity_id=portfolio_id,
        )
        self._append_source_ref(
            refs,
            response=response,
            response_key="holdings",
            section_id="holdings_appendix",
            source_service="lotus-core",
            source_endpoint=f"/portfolios/{portfolio_id}/positions",
            source_entity_id=portfolio_id,
        )
        self._append_source_ref(
            refs,
            response=response,
            response_key="transactions",
            section_id="transactions_appendix",
            source_service="lotus-core",
            source_endpoint=f"/portfolios/{portfolio_id}/transactions",
            source_entity_id=portfolio_id,
        )
        return refs

    def _append_source_ref(
        self,
        refs: list[dict[str, object]],
        *,
        response: dict[str, object],
        response_key: str,
        section_id: str,
        source_service: str,
        source_endpoint: str,
        source_entity_id: str,
        input_services: list[str] | None = None,
    ) -> None:
        if response_key not in response:
            return
        source_ref: dict[str, object] = {
            "section_id": section_id,
            "response_key": response_key,
            "source_service": source_service,
            "source_endpoint": source_endpoint,
            "source_entity_id": source_entity_id,
        }
        if input_services:
            source_ref["input_services"] = input_services
        refs.append(source_ref)

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
                    "market_value_reporting_currency": self._position_market_value(row),
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
            "benchmark": self._performance_benchmark_context(request_payload),
            "supportability": self._performance_supportability(request_payload),
            "methodology": self._review_methodology(request_payload),
        }

    def _annualized_return_supported(self, period: object) -> bool:
        return isinstance(period, str) and period.upper() in {"1Y", "2Y", "5Y", "10Y", "SI"}

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

    def _performance_key_figures(self, performance: dict[str, object]) -> dict[str, object]:
        summary = self._as_dict(performance.get("summary"))
        benchmark = self._as_dict(performance.get("benchmark"))
        return {
            "one_month_net_return_pct": self._period_return(summary, "1M", "net_cumulative_return"),
            "three_month_net_return_pct": self._period_return(
                summary, "3M", "net_cumulative_return"
            ),
            "ytd_net_return_pct": self._period_return(summary, "YTD", "net_cumulative_return"),
            "five_year_net_annualized_return_pct": self._period_return(
                summary, "5Y", "net_annualized_return"
            ),
            "since_inception_net_return_pct": self._period_return(
                summary, "SI", "net_cumulative_return"
            ),
            "benchmark_code": benchmark.get("benchmark_code"),
            "benchmark_comparison_status": benchmark.get("comparison_status"),
        }

    def _risk_key_figures(self, risk: dict[str, object]) -> dict[str, object]:
        summary = self._as_dict(risk.get("summary"))
        ytd = self._as_dict(summary.get("YTD"))
        three_year = self._as_dict(summary.get("THREE_YEAR"))
        return {
            "ytd_volatility_pct": ytd.get("volatility"),
            "ytd_drawdown_pct": ytd.get("drawdown"),
            "ytd_value_at_risk_pct": ytd.get("value_at_risk"),
            "ytd_expected_shortfall_pct": self._expected_shortfall(risk, "YTD"),
            "ytd_risk_adjusted_return": ytd.get("risk_adjusted_return"),
            "three_year_volatility_pct": three_year.get("volatility"),
            "three_year_drawdown_pct": three_year.get("drawdown"),
            "three_year_value_at_risk_pct": three_year.get("value_at_risk"),
            "three_year_expected_shortfall_pct": self._expected_shortfall(risk, "THREE_YEAR"),
            "benchmark_relative_status": self._risk_benchmark_status(risk),
        }

    def _income_activity_key_figures(self, income_activity: dict[str, object]) -> dict[str, object]:
        income = self._as_dict(income_activity.get("incomeSummary"))
        activity = self._as_dict(income_activity.get("activitySummary"))
        return {
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
        }

    def _holdings_key_figures(self, holdings: dict[str, object]) -> dict[str, object]:
        rows = self._holding_rows(holdings)
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
        negative_cash_rows = [
            row
            for row in rows
            if self._safe_str(row.get("asset_class")) == "Cash"
            and self._to_float(row.get("market_value_reporting_currency")) < 0
        ]
        return {
            "position_count": self._to_int(holdings.get("positionCount")),
            "positive_exposure_reporting_currency": positive_exposure,
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

    def _transaction_key_figures(self, transactions: dict[str, object]) -> dict[str, object]:
        by_category = self._as_dict(transactions.get("transactionsByCategory"))
        counts = {
            category: len(self._as_list(rows))
            for category, rows in by_category.items()
            if isinstance(category, str)
        }
        return {
            "transaction_count": self._to_int(transactions.get("transactionCount")),
            "transaction_count_by_category": counts,
        }

    def _review_observations(self, response: dict[str, object]) -> list[dict[str, object]]:
        observations: list[dict[str, object]] = []
        key_figures = self._as_dict(response.get("keyFigures"))
        performance = self._as_dict(key_figures.get("performance"))
        holdings = self._as_dict(key_figures.get("holdings"))
        risk = self._as_dict(response.get("riskAnalytics"))
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

    def _review_report_coverage(self, response: dict[str, object]) -> dict[str, object]:
        key_figures = self._as_dict(response.get("keyFigures"))
        return {
            "status": self._as_dict(response.get("readiness")).get("status", "ready"),
            "figure_groups": [
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
                    "group_id": "targets_guidelines_and_suitability",
                    "status": "not_sourced",
                    "required": False,
                    "message": (
                        "Target allocation, mandate guidelines, risk profile, objectives, "
                        "liquidity needs, and suitability posture are owned by advisory or "
                        "management domains and are not invented by lotus-report."
                    ),
                },
                {
                    "group_id": "tax_lot_and_realized_gain_loss",
                    "status": "not_sourced",
                    "required": False,
                    "message": (
                        "Tax-lot-level realized gain/loss, cost basis, and jurisdiction-specific "
                        "tax treatment are not sourced in the current report response."
                    ),
                },
            ],
        }

    def _figure_group(
        self, group_id: str, key_figures: dict[str, object], *, required: bool
    ) -> dict[str, object]:
        group = self._as_dict(key_figures.get(group_id))
        populated = any(value not in (None, "", {}, []) for value in group.values())
        return {
            "group_id": group_id,
            "status": "present" if populated else "missing",
            "required": required,
        }

    def _benchmark_comparison_coverage(self, response: dict[str, object]) -> str:
        performance = self._as_dict(response.get("performance"))
        benchmark = self._as_dict(performance.get("benchmark"))
        if not benchmark.get("benchmark_code"):
            return "not_requested"
        if benchmark.get("comparison_status") == "available":
            return "present"
        return "partial"

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
        self, request_payload: dict[str, object]
    ) -> dict[str, object]:
        benchmark_code = self._optional_string(request_payload, *BENCHMARK_CODE_KEYS)
        return {
            "benchmark_code": benchmark_code,
            "comparison_status": "unavailable" if benchmark_code else "not_requested",
            "reason_code": "benchmark_return_series_not_sourced" if benchmark_code else None,
        }

    def _performance_supportability(self, request_payload: dict[str, object]) -> dict[str, object]:
        if not self._optional_string(request_payload, *BENCHMARK_CODE_KEYS):
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
            "transaction_type": transaction_type,
            "transaction_category": category,
            "display_label": self._transaction_display_label(transaction_type, cash_leg),
            "cash_leg": cash_leg,
            "asset_class": self._safe_str(row.get("asset_class")) or None,
            "amount_reporting_currency": amount,
            "gross_transaction_amount_reporting_currency": gross_amount,
            "net_interest_amount_reporting_currency": interest_amount,
            "withholding_tax_amount_reporting_currency": tax_amount,
            "income_or_tax_reporting_currency": interest_amount - tax_amount,
        }

    def _transaction_review_category(
        self, *, transaction_type: str, cash_leg: bool, asset_class: str
    ) -> str:
        if cash_leg:
            return "Cash Ledger"
        if transaction_type in {"DIVIDEND", "INTEREST", "COUPON"}:
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

    def _build_risk_stateless_input(
        self,
        *,
        as_of_date: str,
        request_payload: dict[str, object],
        portfolio_open_date: str,
        returns: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "scope": {
                "as_of_date": as_of_date,
                "reporting_currency": self._optional_string(
                    request_payload, *REPORTING_CURRENCY_KEYS
                ),
                "net_or_gross": "NET",
            },
            "periods": [
                {"type": "YTD", "name": "YTD"},
                {"type": "THREE_YEAR", "name": "THREE_YEAR"},
            ],
            "metrics": list(RISK_METRICS),
            "portfolio_open_date": portfolio_open_date,
            "returns": returns,
            "benchmark_returns": [],
        }

    def _position_market_value(self, row: dict[str, object]) -> float:  # monetary-float-allow
        if row.get("market_value_reporting_currency") is not None:
            return self._to_float(row.get("market_value_reporting_currency"))
        valuation = self._as_dict(row.get("valuation"))
        return self._to_float(valuation.get("market_value"))

    @staticmethod
    def _workspace_summary_ready(status_code: int, payload: dict[str, object]) -> bool:
        return status_code < status.HTTP_400_BAD_REQUEST and "results_by_period" in payload

    def _section_items(self, section_id: str, section_payload: object) -> list[dict[str, object]]:
        section = self._as_dict(section_payload)
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
        return [
            {
                "item_type": "performance_period",
                "period": period,
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
                "net_cumulative_return": row.get("net_cumulative_return"),
                "net_annualized_return": row.get("net_annualized_return"),
                "gross_cumulative_return": row.get("gross_cumulative_return"),
                "gross_annualized_return": row.get("gross_annualized_return"),
                "annualized_return_supported": row.get("annualized_return_supported"),
                "benchmark_code": benchmark.get("benchmark_code"),
                "benchmark_comparison_status": benchmark.get("comparison_status"),
            }
            for period, period_payload in summary.items()
            for row in [self._as_dict(period_payload)]
        ]

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
        return items

    def _holding_items(self, holdings: dict[str, object]) -> list[dict[str, object]]:
        items = [
            {
                "item_type": "holdings_summary",
                "position_count": self._to_int(holdings.get("positionCount")),
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
                        "quantity": row.get("quantity"),
                        "market_value_reporting_currency": row.get(
                            "market_value_reporting_currency"
                        ),
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
                        "transaction_type": row.get("transaction_type"),
                        "amount_reporting_currency": row.get("amount_reporting_currency"),
                        "gross_transaction_amount_reporting_currency": row.get(
                            "gross_transaction_amount_reporting_currency"
                        ),
                        "net_interest_amount_reporting_currency": row.get(
                            "net_interest_amount_reporting_currency"
                        ),
                        "withholding_tax_amount_reporting_currency": row.get(
                            "withholding_tax_amount_reporting_currency"
                        ),
                    }
                )
        return items
