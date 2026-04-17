import pytest
from fastapi import HTTPException

from app.services.reporting_read_service import ReportingReadService


class _PasSnapshotMissing:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {"unexpected": "shape"}

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {"unexpected": "shape"}

    async def get_portfolio_transactions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {"unexpected": "shape"}

    async def get_performance_input(
        self,
        portfolio_id: str,
        as_of_date: str,
        lookback_days: int = 1200,
    ):
        return 200, {"unexpected": "shape"}

    async def get_portfolio_positions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {"unexpected": "shape"}


class _PasSuccessMinimal:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "totals": {
                "total_market_value_reporting_currency": 100.0,
                "cash_balance_reporting_currency": 10.0,
                "invested_market_value_reporting_currency": 90.0,
            },
            "snapshot_metadata": {"snapshot_date": payload.get("as_of_date")},
        }

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "scope": {"portfolio_id": portfolio_id},
            "views": [{"dimension": "asset_class", "buckets": []}],
        }

    async def get_portfolio_transactions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "reporting_currency": params.get("reporting_currency", "USD"),
            "total": 2,
            "skip": 0,
            "limit": 500,
            "transactions": [
                {
                    "transaction_id": "TXN-INT-1",
                    "transaction_date": "2026-01-15",
                    "transaction_type": "INTEREST",
                    "net_interest_amount_reporting_currency": 3.0,
                    "gross_transaction_amount_reporting_currency": 3.0,
                },
                {
                    "transaction_id": "TXN-DEP-1",
                    "transaction_date": "2026-02-10",
                    "transaction_type": "DEPOSIT",
                    "gross_transaction_amount_reporting_currency": 5.0,
                },
            ],
        }

    async def get_portfolio_positions(
        self,
        portfolio_id: str,
        params: dict[str, object],
        correlation_id: str | None = None,
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "total": 1,
            "positions": [
                {
                    "security_id": "EQ-1",
                    "instrument_name": "Equity 1",
                    "asset_class": "EQUITY",
                    "quantity": 2,
                    "market_value_reporting_currency": 90.0,
                    "weight": 0.9,
                    "currency": "USD",
                }
            ],
        }

    async def get_performance_input(
        self,
        portfolio_id: str,
        as_of_date: str,
        lookback_days: int = 1200,
    ):
        return 200, {
            "performanceStartDate": "2025-01-01",
            "valuationPoints": [
                {
                    "day": 1,
                    "perf_date": "2025-01-02",
                    "begin_mv": 100.0,
                    "end_mv": 101.0,
                    "bod_cf": 0.0,
                    "eod_cf": 0.0,
                    "mgmt_fees": 0.0,
                }
            ],
        }


class _PaSuccessEmpty:
    async def get_pas_input_twr(self, portfolio_id: str, as_of_date: str, periods: list[str]):
        return 200, {"resultsByPeriod": {"YTD": {"net_cumulative_return": 2.1}}}

    async def calculate_twr(self, payload: dict[str, object]):
        return 200, {
            "results_by_period": {
                "EXPLICIT": {
                    "breakdowns": {
                        "daily": [{"period": "2025-01-02", "summary": {"period_return_pct": 1.0}}]
                    }
                }
            }
        }


class _RiskSuccess:
    async def calculate_risk(self, payload: dict[str, object]):
        return 200, {"results": {"YTD": {"metrics": {"VOLATILITY": {"value": 0.2}}}}}


@pytest.mark.asyncio
async def test_summary_requires_as_of_date():
    service = ReportingReadService(
        pas_client=_PasSuccessMinimal(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    with pytest.raises(HTTPException) as exc:
        await service.get_portfolio_summary("P1", {}, None)
    assert exc.value.status_code == 422
    assert "as_of_date" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_summary_includes_default_sections_when_sections_not_list():
    service = ReportingReadService(
        pas_client=_PasSuccessMinimal(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    response = await service.get_portfolio_summary(
        "P1",
        {"as_of_date": "2026-02-24", "sections": "WEALTH"},
        None,
    )
    assert "wealth" in response
    assert "allocation" in response
    assert "incomeSummary" in response
    assert "activitySummary" in response


@pytest.mark.asyncio
async def test_summary_contract_missing_raises_502():
    service = ReportingReadService(
        pas_client=_PasSnapshotMissing(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    with pytest.raises(HTTPException) as exc:
        await service.get_portfolio_summary("P1", {"as_of_date": "2026-02-24"}, None)
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_summary_rejects_unknown_allocation_dimension():
    service = ReportingReadService(
        pas_client=_PasSuccessMinimal(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    with pytest.raises(HTTPException) as exc:
        await service.get_portfolio_summary(
            "P1",
            {
                "as_of_date": "2026-02-24",
                "sections": ["ALLOCATION"],
                "allocation_dimensions": ["CUSIP"],
            },
            None,
        )
    assert exc.value.status_code == 422
    assert "Unsupported allocation dimension" in str(exc.value.detail)


def test_requested_sections_filters_non_string_values():
    service = ReportingReadService(
        pas_client=_PasSuccessMinimal(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    sections = service._requested_sections(
        request_payload={"sections": ["overview", 10, None, "performance"]},
        default_sections=["OVERVIEW"],
    )
    assert sections == {"OVERVIEW", "PERFORMANCE"}


def test_map_pa_performance_handles_non_dict_rows():
    service = ReportingReadService(
        pas_client=_PasSuccessMinimal(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    mapped = service._map_pa_performance({"resultsByPeriod": {"YTD": "bad-row"}})
    assert mapped["summary"]["YTD"]["net_cumulative_return"] is None


@pytest.mark.asyncio
async def test_review_default_sections_include_all_payload_groups():
    service = ReportingReadService(
        pas_client=_PasSuccessMinimal(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    response = await service.get_portfolio_review("P1", {"as_of_date": "2026-02-24"}, None)
    assert "overview" in response
    assert "allocation" in response
    assert "incomeAndActivity" in response
    assert "holdings" in response
    assert "transactions" in response
    assert "riskAnalytics" in response


@pytest.mark.asyncio
async def test_review_without_risk_section_omits_risk_block():
    service = ReportingReadService(
        pas_client=_PasSuccessMinimal(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    response = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["overview", "holdings"]},
        None,
    )
    assert "overview" in response
    assert "holdings" in response
    assert "riskAnalytics" not in response


@pytest.mark.asyncio
async def test_summary_with_explicit_sections_can_exclude_wealth_and_allocation():
    service = ReportingReadService(
        pas_client=_PasSuccessMinimal(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    response = await service.get_portfolio_summary(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["pnl"]},
        None,
    )
    assert response["pnlSummary"]["total_pnl"] == 10.0
    assert "wealth" not in response
    assert "allocation" not in response


def test_to_float_returns_zero_for_non_numeric_string():
    assert ReportingReadService._to_float("not-a-number") == 0.0


def test_to_float_accepts_numeric_values():
    assert ReportingReadService._to_float(7) == 7.0


def test_to_float_returns_zero_for_unsupported_type():
    assert ReportingReadService._to_float(object()) == 0.0


class _PasPerfStatusError(_PasSuccessMinimal):
    async def get_performance_input(
        self,
        portfolio_id: str,
        as_of_date: str,
        lookback_days: int = 1200,
    ):
        return 500, {"detail": "upstream error"}


class _PasPerfInvalidPoints(_PasSuccessMinimal):
    async def get_performance_input(
        self,
        portfolio_id: str,
        as_of_date: str,
        lookback_days: int = 1200,
    ):
        return 200, {"performanceStartDate": "2025-01-01", "valuationPoints": []}


class _PasPerfInvalidStart(_PasSuccessMinimal):
    async def get_performance_input(
        self,
        portfolio_id: str,
        as_of_date: str,
        lookback_days: int = 1200,
    ):
        return 200, {"performanceStartDate": None, "valuationPoints": [{"perf_date": "2025-01-01"}]}


class _PaTwrStatusError(_PaSuccessEmpty):
    async def calculate_twr(self, payload: dict[str, object]):
        return 500, {"detail": "twr failed"}


class _PaTwrNoReturns(_PaSuccessEmpty):
    async def calculate_twr(self, payload: dict[str, object]):
        return 200, {"results_by_period": {"EXPLICIT": {"breakdowns": {"daily": []}}}}


class _RiskStatusError(_RiskSuccess):
    async def calculate_risk(self, payload: dict[str, object]):
        return 500, {"detail": "risk failed"}


@pytest.mark.asyncio
async def test_build_risk_analytics_returns_none_on_performance_input_failure():
    service = ReportingReadService(
        pas_client=_PasPerfStatusError(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    result = await service._build_risk_analytics("P1", "2026-02-24")
    assert result is None


@pytest.mark.asyncio
async def test_build_risk_analytics_returns_none_on_invalid_valuation_points():
    service = ReportingReadService(
        pas_client=_PasPerfInvalidPoints(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    result = await service._build_risk_analytics("P1", "2026-02-24")
    assert result is None


@pytest.mark.asyncio
async def test_build_risk_analytics_returns_none_on_invalid_performance_start_date():
    service = ReportingReadService(
        pas_client=_PasPerfInvalidStart(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    result = await service._build_risk_analytics("P1", "2026-02-24")
    assert result is None


@pytest.mark.asyncio
async def test_build_risk_analytics_returns_none_when_twr_call_fails():
    service = ReportingReadService(
        pas_client=_PasSuccessMinimal(),
        pa_client=_PaTwrStatusError(),
        risk_client=_RiskSuccess(),
    )
    result = await service._build_risk_analytics("P1", "2026-02-24")
    assert result is None


@pytest.mark.asyncio
async def test_build_risk_analytics_returns_none_when_daily_returns_empty():
    service = ReportingReadService(
        pas_client=_PasSuccessMinimal(),
        pa_client=_PaTwrNoReturns(),
        risk_client=_RiskSuccess(),
    )
    result = await service._build_risk_analytics("P1", "2026-02-24")
    assert result is None


@pytest.mark.asyncio
async def test_build_risk_analytics_returns_none_when_risk_call_fails():
    service = ReportingReadService(
        pas_client=_PasSuccessMinimal(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskStatusError(),
    )
    result = await service._build_risk_analytics("P1", "2026-02-24")
    assert result is None


def test_extract_daily_returns_skips_invalid_items():
    service = ReportingReadService(
        pas_client=_PasSuccessMinimal(),
        pa_client=_PaSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    twr_payload = {
        "results_by_period": {
            "EXPLICIT": {
                "breakdowns": {
                    "daily": [
                        "bad",
                        {"period": "2025-01-03", "summary": {"period_return_pct": "bad"}},
                        {"period": 123, "summary": {"period_return_pct": 1.2}},
                        {"period": "2025-01-04", "summary": {"period_return_pct": 0.4}},
                    ]
                }
            }
        }
    }
    returns = service._extract_daily_returns_from_twr(twr_payload)
    assert returns == [{"date": "2025-01-04", "value": 0.4}]
