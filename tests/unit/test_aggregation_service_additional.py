from datetime import date

import pytest

from app.application_errors import ReportingUpstreamError
from app.services.aggregation_service import AggregationService


class _CoreQueryOkClient:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "totals": {"total_market_value_reporting_currency": 1000.0},
            "snapshot_metadata": {"position_count": 0},
        }

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        return 200, {"views": []}


class _PerformanceOkClient:
    async def get_workspace_summary(
        self, payload: dict[str, object], *, admitted_tenant_id: str = ""
    ):
        return (
            200,
            {
                "results_by_period": {
                    "YTD": {
                        "portfolio_twr": {"net": {"summary": {"cumulative_return": {"base": 1.0}}}}
                    }
                }
            },
        )


class _CoreQueryFailClient:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        return 503, {"detail": "down"}

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        return 503, {"detail": "down"}


class _PerformanceFailClient:
    async def get_workspace_summary(
        self, payload: dict[str, object], *, admitted_tenant_id: str = ""
    ):
        return 503, {"detail": "down"}


def test_build_asset_class_rows_sorts_and_ignores_non_positive_values():
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceOkClient()
    )
    payload = {
        "allocation": {
            "views": [
                {
                    "dimension": "asset_class",
                    "buckets": [
                        {
                            "dimension_value": "BOND",
                            "weight": 0.25,
                            "market_value_reporting_currency": 25,
                        },
                        {
                            "dimension_value": "EQUITY",
                            "weight": 0.75,
                            "market_value_reporting_currency": 75,
                        },
                        {
                            "dimension_value": "CASH",
                            "weight": -0.05,
                            "market_value_reporting_currency": -5,
                        },
                    ],
                }
            ]
        }
    }
    rows = service._build_asset_class_rows(core_query_payload=payload, total_mv=100.0)
    assert [row.bucket for row in rows] == ["BOND", "EQUITY"]
    row_map = {row.bucket: row.value for row in rows}
    assert row_map["BOND"] == 25.0
    assert row_map["EQUITY"] == 75.0


@pytest.mark.parametrize(
    ("payload", "total_mv"),
    [
        ({}, 100.0),
        ({"allocation": []}, 100.0),
        ({"allocation": {"views": []}}, 100.0),
        ({"allocation": {"views": [{"dimension": "asset_class", "buckets": "bad"}]}}, 100.0),
        ({"allocation": {"views": [{"dimension": "asset_class", "buckets": []}]}}, 0.0),
    ],
)
def test_build_asset_class_rows_handles_non_conforming_payloads(payload, total_mv):
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceOkClient()
    )
    assert service._build_asset_class_rows(core_query_payload=payload, total_mv=total_mv) == []


@pytest.mark.asyncio
async def test_fetch_inputs_refuses_rather_than_emptying_a_failed_summary():
    """Absence is carried forward, not flattened into an empty dict.

    This asserted `core_query_payload == {"summary": {}, "allocation": {}}` --
    the exact erasure that let the caller substitute constants for the missing
    values. An emptied payload and a portfolio that holds nothing are the same
    object, so nothing downstream could tell them apart.

    The summary is required, so its failure refuses. Allocation and performance
    are additive, so their failure is recorded in `unavailable_sources` and the
    rows they would have contributed are simply absent.
    """
    service = AggregationService(
        core_query_client=_CoreQueryFailClient(), performance_client=_PerformanceFailClient()
    )

    with pytest.raises(ReportingUpstreamError):
        await service._fetch_inputs("P1", date(2026, 2, 24), admitted_tenant_id="tenant-test")


@pytest.mark.asyncio
async def test_fetch_inputs_records_an_additive_source_that_did_not_answer():
    """A failed performance call loses its row and says so.

    The return row is absent from `rows` and lotus-performance appears in
    `unavailable_sources`, so a consumer can tell an unmeasured return from a
    portfolio that returned zero.
    """
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceFailClient()
    )

    response = await service.get_portfolio_aggregation_live(
        "P1", date(2026, 2, 24), admitted_tenant_id="tenant-test"
    )

    metrics = {row.metric for row in response.rows}
    assert "return_ytd_pct" not in metrics, "an unmeasured return must not be reported as 0"
    assert [source.service for source in response.unavailable_sources] == ["lotus-performance"]
    assert response.unavailable_sources[0].status_code >= 400


def test_build_asset_class_rows_returns_empty_when_total_market_value_non_positive():
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceOkClient()
    )
    payload = {
        "allocation": {
            "views": [
                {
                    "dimension": "asset_class",
                    "buckets": [
                        {
                            "dimension_value": "EQUITY",
                            "weight": 0.3,
                            "market_value_reporting_currency": 30,
                        }
                    ],
                }
            ]
        }
    }
    assert service._build_asset_class_rows(core_query_payload=payload, total_mv=-1.0) == []


def test_build_asset_class_rows_ignores_non_dict_positions():
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceOkClient()
    )
    payload = {
        "allocation": {
            "views": [
                {
                    "dimension": "asset_class",
                    "buckets": [
                        "bad",
                        {"dimension_value": "EQUITY", "market_value_reporting_currency": 20},
                    ],
                }
            ]
        }
    }
    rows = service._build_asset_class_rows(core_query_payload=payload, total_mv=100.0)
    assert len(rows) == 1
    assert rows[0].bucket == "EQUITY"
    assert rows[0].value == 20.0


def test_build_asset_class_rows_handles_invalid_bucket_fields_and_weight_fallback():
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformanceOkClient()
    )
    payload = {
        "allocation": {
            "views": [
                "bad-view",
                {
                    "dimension": "asset_class",
                    "buckets": [
                        {"dimension_value": " ", "market_value_reporting_currency": 20},
                        {
                            "dimension_value": "BROKEN",
                            "market_value_reporting_currency": "not-money",
                        },
                        {
                            "dimension_value": "EQUITY",
                            "weight": "not-a-weight",
                            "market_value_reporting_currency": 40,
                        },
                    ],
                },
            ]
        }
    }

    rows = service._build_asset_class_rows(core_query_payload=payload, total_mv=200.0)

    assert len(rows) == 1
    assert rows[0].bucket == "EQUITY"
    assert rows[0].value == 20.0


class _CoreQueryMalformedAllocation:
    def __init__(self, views):
        self._views = views

    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        return 200, {
            "portfolio_id": portfolio_id,
            "totals": {"total_market_value_reporting_currency": 250.0},
            "snapshot_metadata": {"position_count": 0},
        }

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        return 200, {"views": self._views}


@pytest.mark.parametrize(
    "views",
    [
        "bad-views",
        [{"dimension": "asset_class", "buckets": "bad-map"}],
        [{"dimension": "asset_class", "buckets": ["bad-list"]}],
    ],
)
@pytest.mark.asyncio
async def test_live_aggregation_handles_malformed_allocation_shapes(views):
    service = AggregationService(
        core_query_client=_CoreQueryMalformedAllocation(views),
        performance_client=_PerformanceOkClient(),
    )
    response = await service.get_portfolio_aggregation_live(
        "P1", date(2026, 2, 24), admitted_tenant_id="tenant-test"
    )
    metric_map = {row.metric: row.value for row in response.rows}
    assert metric_map["market_value_base"] == 250.0
    assert metric_map["position_count"] == 0.0


class _CoreQueryMalformedSummary:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        _ = portfolio_id, payload, correlation_id
        return 200, {
            "totals": ["bad"],
            "snapshot_metadata": "bad",
        }

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        _ = portfolio_id, payload, correlation_id
        return 200, {"views": []}


class _PerformanceMissingYtd:
    async def get_workspace_summary(
        self, payload: dict[str, object], *, admitted_tenant_id: str = ""
    ):
        _ = payload
        return 200, {"results_by_period": {}}


class _CoreQueryInvalidPositionCount(_CoreQueryMalformedSummary):
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        _ = portfolio_id, payload, correlation_id
        return 200, {
            "totals": {"total_market_value_reporting_currency": 10.0},
            "snapshot_metadata": {"position_count": "not-int"},
        }


@pytest.mark.asyncio
async def test_live_aggregation_omits_an_unreadable_position_count():
    """An unreadable count is absent, not zero.

    This asserted `position_count == 0.0` for a value the summary could not
    express as an integer -- which is the same number a genuinely empty
    portfolio reports, so a consumer could not tell "no positions" from "the
    source said something we could not read".
    """
    service = AggregationService(
        core_query_client=_CoreQueryInvalidPositionCount(),
        performance_client=_PerformanceOkClient(),
    )

    response = await service.get_portfolio_aggregation_live(
        "P1", date(2026, 2, 24), admitted_tenant_id="tenant-test"
    )

    metrics = {row.metric for row in response.rows}
    assert "position_count" not in metrics, (
        "an unreadable count must be omitted, because 0 is what an empty portfolio reports"
    )
    assert "market_value_base" in metrics, "the rows the summary did supply are still returned"


class _CoreQueryNestedInvalidSummary:
    async def get_portfolio_summary(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        _ = portfolio_id, payload, correlation_id
        return 200, {"summary": "invalid"}

    async def get_asset_allocation(
        self,
        portfolio_id: str,
        payload: dict[str, object],
        correlation_id: str | None = None,
        *,
        admitted_tenant_id: str = "",
    ):
        _ = portfolio_id, payload, correlation_id
        return 200, {"views": []}


class _AggregationServiceWithMalformedFetchedSummary(AggregationService):
    async def _fetch_inputs(
        self, portfolio_id: str, as_of_date: date, admitted_tenant_id="tenant-test"
    ):
        _ = portfolio_id, as_of_date
        return {"summary": "invalid", "allocation": {"views": []}}, {}


class _CoreQueryAllocationFails:
    async def get_portfolio_summary(self, **_: object) -> tuple[int, dict[str, object]]:
        return 200, {
            "totals": {"total_market_value_reporting_currency": 100.0},
            "snapshot_metadata": {"position_count": 2},
        }

    async def get_asset_allocation(self, **_: object) -> tuple[int, dict[str, object]]:
        return 503, {"detail": "unavailable"}


@pytest.mark.asyncio
async def test_an_unavailable_allocation_loses_its_rows_and_is_recorded() -> None:
    """Allocation is additive, so its failure is reported rather than refused.

    Its absence costs the per-asset-class rows and nothing else. Recording it
    is what stops a consumer reading "no asset classes" as a portfolio holding
    a single undifferentiated lump.
    """
    service = AggregationService(
        core_query_client=_CoreQueryAllocationFails(),
        performance_client=_PerformanceOkClient(),
    )

    response = await service.get_portfolio_aggregation_live(
        "P1", date(2026, 2, 24), admitted_tenant_id="tenant-test"
    )

    assert [source.service for source in response.unavailable_sources] == ["lotus-core"]
    assert response.unavailable_sources[0].endpoint.endswith("asset-allocation/query")
    assert all(row.bucket == "TOTAL" for row in response.rows), (
        "no per-asset-class rows can be built from an allocation that never arrived"
    )


class _CoreQueryNonDictSections:
    """A 200 whose `totals` and `snapshot_metadata` are not objects."""

    async def get_portfolio_summary(self, **_: object) -> tuple[int, dict[str, object]]:
        return 200, {"totals": "not-a-dict", "snapshot_metadata": ["not-a-dict"]}

    async def get_asset_allocation(self, **_: object) -> tuple[int, dict[str, object]]:
        return 200, {}


@pytest.mark.asyncio
async def test_a_summary_with_unreadable_sections_is_refused_not_defaulted() -> None:
    """Malformed shapes reach the same refusal as a missing total.

    The defensive `isinstance` guards normalise an unreadable `totals` to an
    empty mapping -- which then has no market value, so the response refuses
    rather than continuing with substituted numbers. The guards make the shape
    safe to read; they do not make up a value.
    """
    service = AggregationService(
        core_query_client=_CoreQueryNonDictSections(),
        performance_client=_PerformanceOkClient(),
    )

    with pytest.raises(ReportingUpstreamError) as refusal:
        await service.get_portfolio_aggregation_live(
            "P1", date(2026, 2, 24), admitted_tenant_id="tenant-test"
        )

    assert refusal.value.detail["code"] == "aggregation_source_incomplete"


@pytest.mark.parametrize(
    "value",
    [None, True, False, "not-a-number", 3.7, {"count": 1}],
    ids=["none", "true", "false", "text", "float", "mapping"],
)
def test_an_optional_int_admits_only_what_a_count_can_be(value: object) -> None:
    """`True` is the case that matters: `bool` is an `int` subclass.

    Without the explicit bool check a `position_count` of `True` is admitted as
    1 -- a count invented from a flag. A float is rejected too: 3.7 positions is
    not a count, and truncating it would report a number the source never gave.
    """
    assert AggregationService._optional_int(value) is None


@pytest.mark.parametrize(("value", "expected"), [(0, 0), (7, 7), ("12", 12)])
def test_an_optional_int_accepts_an_integer_the_source_supplied(
    value: object, expected: int
) -> None:
    """Zero is a real count and must survive: that is the whole distinction."""
    assert AggregationService._optional_int(value) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results_by_period": "not-a-dict"},
        {"results_by_period": {"YTD": None}},
        {"results_by_period": {"YTD": {"portfolio_twr": "not-a-dict"}}},
    ],
    ids=["empty", "not-a-dict", "missing-branch", "non-dict-branch"],
)
def test_an_unreadable_performance_payload_yields_no_return(payload: dict[str, object]) -> None:
    """Every shape that cannot produce a return produces None, never 0.0.

    A zero return is a fact about a flat year. These are facts about a payload
    that did not contain one.
    """
    assert AggregationService._ytd_return(payload) is None


class _PerformancePending:
    """The accepted envelope a bounded poll returns when the job is still running."""

    async def get_workspace_summary(
        self, *_: object, **__: object
    ) -> tuple[int, dict[str, object]]:
        return 202, {"result_path": "/performance/results/abc"}


@pytest.mark.asyncio
async def test_a_pending_performance_job_is_reported_as_pending_not_complete() -> None:
    """202 is a calculation still running, not one that finished with no return.

    Found in review. The guard was `>= 400`, so the accepted envelope returned
    after the client's polling budget looked like a successful response
    carrying no return: the row was dropped and `unavailable_sources` stayed
    empty. That is the empty-versus-unavailable ambiguity this change exists to
    remove, reintroduced one status code to the left.
    """
    service = AggregationService(
        core_query_client=_CoreQueryOkClient(), performance_client=_PerformancePending()
    )

    response = await service.get_portfolio_aggregation_live(
        "P1", date(2026, 2, 24), admitted_tenant_id="tenant-test"
    )

    assert "return_ytd_pct" not in {row.metric for row in response.rows}
    pending = [s for s in response.unavailable_sources if s.service == "lotus-performance"]
    assert pending, "a still-running calculation must be reported, not silently dropped"
    assert pending[0].reason == "pending"
    assert pending[0].status_code == 202


class _CoreQueryNonNumericTotal:
    async def get_portfolio_summary(self, **_: object) -> tuple[int, dict[str, object]]:
        return 200, {
            "totals": {"total_market_value_reporting_currency": "n/a"},
            "snapshot_metadata": {"position_count": 1},
        }

    async def get_asset_allocation(self, **_: object) -> tuple[int, dict[str, object]]:
        return 200, {}


@pytest.mark.asyncio
async def test_a_non_numeric_market_value_is_an_upstream_error_not_a_crash() -> None:
    """`"n/a"` is an invalid upstream contract, not a fault of ours.

    Found in review. The conversion raised `ValueError`, and the router catches
    only `ReportingApplicationError`, so an upstream contract violation became
    an unstructured 500 that named nothing -- instead of the documented 502
    naming lotus-core.
    """
    service = AggregationService(
        core_query_client=_CoreQueryNonNumericTotal(), performance_client=_PerformanceOkClient()
    )

    with pytest.raises(ReportingUpstreamError) as refusal:
        await service.get_portfolio_aggregation_live(
            "P1", date(2026, 2, 24), admitted_tenant_id="tenant-test"
        )

    assert refusal.value.detail["service"] == "lotus-core"
    assert refusal.value.detail["code"] == "aggregation_source_incomplete"


@pytest.mark.asyncio
async def test_an_unreadable_position_count_is_reported_as_incomplete_evidence() -> None:
    """Omitting the row is not enough; the omission has to be explainable.

    Found in review. The summary succeeded, so `unavailable_sources` stayed
    empty while the row vanished -- leaving a consumer unable to tell malformed
    upstream evidence from a metric this response simply does not carry, which
    is the ambiguity the whole change is about.
    """
    service = AggregationService(
        core_query_client=_CoreQueryInvalidPositionCount(),
        performance_client=_PerformanceOkClient(),
    )

    response = await service.get_portfolio_aggregation_live(
        "P1", date(2026, 2, 24), admitted_tenant_id="tenant-test"
    )

    assert "position_count" not in {row.metric for row in response.rows}
    incomplete = [s for s in response.unavailable_sources if s.reason == "incomplete_payload"]
    assert incomplete, "a summary that omitted the count must say so"
    assert incomplete[0].service == "lotus-core"
    assert incomplete[0].status_code == 200, "the call succeeded; the payload did not carry it"
