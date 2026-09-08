"""Report sends the admitted tenant to lotus-core, on every route (#365).

Report already sent `X-Tenant-Id` to lotus-ai, lotus-archive and -- since #361
-- lotus-performance. **lotus-core received it from none of them**, including
the batch scheduler's portfolio-detail reads. Core is the service that owns
portfolio ownership and the one placed to refuse a foreign portfolio, so it was
the worst of the four to be missing.

The assertions are on the headers the transport actually received. A test that
checks the argument reached the client proves the call signature and nothing
about the wire, which is the half that matters to a service deciding whether a
caller may see a portfolio.

Registered routes are enumerated from the client rather than listed here: a
seventh Core method added later must fail this file rather than quietly ship
without a tenant.
"""

from __future__ import annotations

import inspect
from datetime import date
from typing import Any

import pytest

from app.clients.core_query_client import CoreQueryClient

#: Every public Core call, and the arguments it needs beyond the tenant. Derived
#: from the client so a new method cannot be missed -- see the module docstring.
CALLS: dict[str, dict[str, Any]] = {
    "get_portfolio_summary": {"portfolio_id": "P1", "payload": {"as_of_date": "2026-02-24"}},
    "get_asset_allocation": {"portfolio_id": "P1", "payload": {"dimensions": ["asset_class"]}},
    "get_portfolio_transactions": {"portfolio_id": "P1", "params": {"limit": 10}},
    "get_portfolio_positions": {"portfolio_id": "P1", "params": {"limit": 10}},
    "get_portfolio_detail": {"portfolio_id": "P1"},
    "get_portfolio_review": {"portfolio_id": "P1", "payload": {"as_of_date": "2026-02-24"}},
}


def test_every_public_core_method_is_covered_here() -> None:
    """The enumeration is derived, not restated.

    A hand-written list of six is satisfied by a seventh method that ships with
    no tenant at all. This fails instead.
    """
    public = {
        name
        for name, member in inspect.getmembers(CoreQueryClient, inspect.isfunction)
        if name.startswith("get_")
    }

    assert public == set(CALLS), (
        f"CoreQueryClient's public surface changed: {sorted(public ^ set(CALLS))}. "
        "A new Core call must be covered here before it ships."
    )


class _RecordingTransport:
    """Captures the headers each request actually carried."""

    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []

    async def __call__(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        self.headers.append(dict(kwargs["headers"]))
        return 200, {}


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> _RecordingTransport:
    recorder = _RecordingTransport()
    monkeypatch.setattr("app.clients.core_query_client.post_with_retry", recorder)
    monkeypatch.setattr("app.clients.core_query_client.get_with_retry", recorder)
    return recorder


def _client() -> CoreQueryClient:
    return CoreQueryClient(base_url="http://core", timeout_seconds=1.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", sorted(CALLS))
async def test_the_admitted_tenant_reaches_the_wire(
    method: str, transport: _RecordingTransport
) -> None:
    """Every Core call carries the admitted tenant, POST and GET alike.

    Parameterised over all six rather than the one the defect was found
    through: it was per-method, and fixing only the summary path would have left
    the same gap on the other five.
    """
    await getattr(_client(), method)(
        **CALLS[method], correlation_id="corr-1", admitted_tenant_id="tenant-sg"
    )

    assert transport.headers, "no request was made"
    assert transport.headers[0].get("X-Tenant-Id") == "tenant-sg"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", sorted(CALLS))
async def test_the_tenant_survives_an_absent_correlation_id(
    method: str, transport: _RecordingTransport
) -> None:
    """The second defect in the same three lines.

    `_headers` returned `{}` whenever the correlation id was falsy, so a call
    without correlation was also a call without tenant, trace and request
    identity. The two are independent: a missing correlation id costs the
    correlation header and nothing else.
    """
    await getattr(_client(), method)(
        **CALLS[method], correlation_id=None, admitted_tenant_id="tenant-sg"
    )

    assert transport.headers[0].get("X-Tenant-Id") == "tenant-sg"


@pytest.mark.asyncio
async def test_absence_is_sent_as_absence_not_a_substitute(
    transport: _RecordingTransport,
) -> None:
    """A blank tenant is sent blank, never defaulted to a name.

    The header is present either way, so a regression that stops threading the
    value looks different from a legitimately tenantless call. Substituting a
    default or a service name would manufacture an ownership claim
    indistinguishable from a real one -- the defect #177 removed from the batch
    scheduler.
    """
    await _client().get_portfolio_summary(
        portfolio_id="P1", payload={}, correlation_id="corr-1", admitted_tenant_id=""
    )

    sent = transport.headers[0]
    assert "X-Tenant-Id" in sent, "the header must be present even when the tenant is absent"
    assert sent["X-Tenant-Id"] == ""
    assert sent["X-Tenant-Id"] not in {"lotus-report", "default", "unknown"}


@pytest.mark.asyncio
async def test_the_correlation_and_trace_headers_are_not_displaced(
    transport: _RecordingTransport,
) -> None:
    """Adding the tenant must not cost the propagation headers.

    They are how an operator joins this call to the request that caused it, and
    a merge that replaced the dict rather than extending it would drop them
    silently while every tenant assertion above still passed.
    """
    await _client().get_portfolio_review(
        portfolio_id="P1", payload={}, correlation_id="corr-9", admitted_tenant_id="tenant-sg"
    )

    sent = transport.headers[0]
    assert sent["X-Tenant-Id"] == "tenant-sg"
    assert sent["X-Correlation-Id"] == "corr-9"


class _TenantScopedCore:
    """A Core that answers only for portfolios the admitted tenant owns.

    Stands in for the real service's ownership boundary. The point of the two
    tests below is not that this fake enforces scoping -- it is that Report now
    transmits the value the boundary needs. Before the fix both calls below were
    identical on the wire, so no upstream could have told them apart.
    """

    OWNERSHIP = {"P-OWNED": "tenant-sg", "P-FOREIGN": "tenant-hk"}

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    async def __call__(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        tenant = dict(kwargs["headers"]).get("X-Tenant-Id", "")
        portfolio = str(kwargs["url"]).rsplit("/", 1)[-1]
        self.seen.append((portfolio, tenant))
        if self.OWNERSHIP.get(portfolio) != tenant:
            return 404, {"detail": "not found"}
        return 200, {"portfolio_id": portfolio}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("portfolio_id", "expected_status"),
    [("P-OWNED", 200), ("P-FOREIGN", 404)],
)
async def test_core_can_distinguish_owned_from_foreign_portfolios(
    portfolio_id: str, expected_status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The outcome the propagation exists for.

    A tenant-scoped Core returns the portfolio it owns and refuses one it does
    not. This is only possible because the tenant is on the request: with the
    previous client both calls carried identical headers, so the two cases were
    indistinguishable and every Report query was answered as though unscoped.
    """
    core = _TenantScopedCore()
    monkeypatch.setattr("app.clients.core_query_client.get_with_retry", core)

    status_code, _ = await _client().get_portfolio_detail(
        portfolio_id=portfolio_id, correlation_id="corr-1", admitted_tenant_id="tenant-sg"
    )

    assert status_code == expected_status
    assert core.seen == [(portfolio_id, "tenant-sg")]


class _CapturingPerformanceClient:
    """Records the tenant each Performance call carried."""

    def __init__(self) -> None:
        self.tenants: list[str] = []

    async def get_workspace_summary(
        self, payload: dict[str, Any], *, admitted_tenant_id: str
    ) -> tuple[int, dict[str, Any]]:
        self.tenants.append(admitted_tenant_id)
        return 200, {"results_by_period": {}}


class _QuietCoreClient:
    async def get_portfolio_summary(self, **_: Any) -> tuple[int, dict[str, Any]]:
        return 200, {"totals": {}, "snapshot_metadata": {}}

    async def get_asset_allocation(self, **_: Any) -> tuple[int, dict[str, Any]]:
        return 200, {}


@pytest.mark.asyncio
async def test_the_aggregation_path_carries_the_tenant_to_performance() -> None:
    """`/aggregations/portfolios/{portfolio_id}` sent `""` deliberately, and no longer does.

    That blank was argued for on the grounds that the route admitted no tenant,
    which was true of the route as it stood and was an argument for changing the
    route rather than for leaving every aggregation Report requested
    unattributable at lotus-performance.

    Held here because falsification found nothing covering it: reverting the
    call to `admitted_tenant_id=""` passed the entire suite. A fix no test can
    see is a fix that comes back.
    """
    from app.services.aggregation_service import AggregationService

    performance = _CapturingPerformanceClient()
    service = AggregationService(
        core_query_client=_QuietCoreClient(),  # type: ignore[arg-type]
        performance_client=performance,  # type: ignore[arg-type]
    )

    await service.get_portfolio_aggregation_live(
        portfolio_id="P1",
        as_of_date=date(2026, 2, 24),
        admitted_tenant_id="tenant-sg",
    )

    assert performance.tenants == ["tenant-sg"], (
        "the aggregation path must transmit the admitted tenant, not a deliberate blank"
    )
