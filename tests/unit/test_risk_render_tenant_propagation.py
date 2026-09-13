"""Report sends the admitted tenant to lotus-risk and lotus-render (#375).

The last two clients that sent no tenant at all: nine methods, every risk call
Report actually makes being `input_mode: "stateful"` — the class lotus-risk's
receiving contract (risk#297) refuses without an admitted tenant — and the
render submission being the origin of a custody chain that ends in a retained
document (C6-X04; Render's receiving side is lotus-render's admit-if-present
rollout recorded on #375).

The assertions are on the headers the transport actually received, per the
issue: a test that checks the argument reached the client proves the call
signature and nothing about the wire.

Render's surface is PARTITIONED rather than merely covered: template and
metadata discovery are global publication authority, deliberately untenanted —
adding tenant scope there is the meaningless-global-scope the Cycle 6 steering
forbids. The partition test makes a new render method fail this file until it
is classified one way or the other.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.clients.render_client import RenderClient
from app.clients.risk_client import RiskClient

#: Every RiskClient call and its non-tenant arguments. All four are
#: tenant-owned: Report only ever asks stateful questions about tenant-owned
#: portfolios, verified across every payload builder when this landed.
RISK_CALLS: dict[str, dict[str, Any]] = {
    "rolling_metrics": {"payload": {"input_mode": "stateful"}},
    "historical_attribution": {"payload": {"input_mode": "stateful"}},
    "drawdown_analytics": {"payload": {"input_mode": "stateful"}},
    "calculate_risk": {"payload": {"input_mode": "stateful"}},
}

#: Render's tenant-owned methods: job identity is created at submission and
#: read back by id, so all three bind to the admitted tenant.
RENDER_TENANT_OWNED: dict[str, dict[str, Any]] = {
    "submit_render_package": {"payload": {"render_job_id": "rj-1"}},
    "get_render_status": {"render_job_id": "rj-1"},
    "get_render_diagnostics": {"render_job_id": "rj-1"},
}

#: Global-by-design: the published template catalogue and service metadata
#: carry no tenant claim at all — not even a blank one.
RENDER_GLOBAL: dict[str, dict[str, Any]] = {
    "get_template_projection": {},
    "get_metadata": {},
}


def _public_async_methods(client_type: type) -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(client_type, inspect.isfunction)
        if not name.startswith("_")
    }


def test_every_risk_method_is_covered_here() -> None:
    """Derived, not restated: a fifth Risk call must fail this file first."""
    assert _public_async_methods(RiskClient) == set(RISK_CALLS), (
        f"RiskClient's public surface changed: "
        f"{sorted(_public_async_methods(RiskClient) ^ set(RISK_CALLS))}. "
        "A new Risk call must be covered here before it ships."
    )


def test_every_render_method_is_classified_tenant_owned_or_global() -> None:
    """The partition is the contract (#375): nothing ships unclassified.

    A new render method that is neither in the tenant-owned set nor in the
    global set fails here, so the classification decision is forced at the
    moment the method is added rather than discovered in a later audit.
    """
    classified = set(RENDER_TENANT_OWNED) | set(RENDER_GLOBAL)
    assert not (set(RENDER_TENANT_OWNED) & set(RENDER_GLOBAL))
    assert _public_async_methods(RenderClient) == classified, (
        f"RenderClient's public surface changed: "
        f"{sorted(_public_async_methods(RenderClient) ^ classified)}. "
        "Classify the new method as tenant-owned or global-by-design here."
    )


class _RecordingTransport:
    """Captures the headers each request actually carried."""

    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []

    async def __call__(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        self.headers.append(dict(kwargs["headers"]))
        return 200, {}


@pytest.fixture
def risk_transport(monkeypatch: pytest.MonkeyPatch) -> _RecordingTransport:
    recorder = _RecordingTransport()
    monkeypatch.setattr("app.clients.risk_client.post_with_retry", recorder)
    monkeypatch.setattr(
        "app.clients.risk_client.propagation_headers",
        lambda: {"X-Correlation-Id": "corr-prop"},
    )
    return recorder


@pytest.fixture
def render_transport(monkeypatch: pytest.MonkeyPatch) -> _RecordingTransport:
    recorder = _RecordingTransport()
    monkeypatch.setattr("app.clients.render_client.post_with_retry", recorder)
    monkeypatch.setattr("app.clients.render_client.get_with_retry", recorder)
    return recorder


def _risk_client() -> RiskClient:
    return RiskClient(base_url="http://risk", timeout_seconds=1.0)


def _render_client() -> RenderClient:
    return RenderClient(
        base_url="http://render",
        timeout_seconds=1.0,
        max_retries=1,
        retry_backoff_seconds=0.0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", sorted(RISK_CALLS))
async def test_the_admitted_tenant_reaches_the_risk_wire(
    method: str, risk_transport: _RecordingTransport
) -> None:
    """Every Risk call carries the admitted tenant.

    Parameterised over all four rather than the one a defect might be found
    through: the gap was per-method, and lotus-risk records whatever arrives
    against no tenant at all — unattributable in principle, not merely
    un-backfilled.
    """
    await getattr(_risk_client(), method)(**RISK_CALLS[method], admitted_tenant_id="tenant-sg")

    assert risk_transport.headers, "no request was made"
    assert risk_transport.headers[0].get("X-Tenant-Id") == "tenant-sg"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", sorted(RENDER_TENANT_OWNED))
async def test_the_admitted_tenant_reaches_the_render_wire(
    method: str, render_transport: _RecordingTransport
) -> None:
    """Every job-scoped Render call carries the admitted tenant."""
    await getattr(_render_client(), method)(
        **RENDER_TENANT_OWNED[method],
        correlation_id="corr-1",
        trace_id=None,
        admitted_tenant_id="tenant-sg",
    )

    assert render_transport.headers, "no request was made"
    assert render_transport.headers[0].get("X-Tenant-Id") == "tenant-sg"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", sorted(RENDER_GLOBAL))
async def test_global_render_methods_carry_no_tenant_claim(
    method: str, render_transport: _RecordingTransport
) -> None:
    """The explicit no-change half of the classification, pinned.

    These are global publication authority; a tenant header here — even a
    blank one — would state a scope the resource does not have. This is the
    recorded no-change decision from #375, as a test rather than prose.
    """
    await getattr(_render_client(), method)(correlation_id="corr-1", trace_id=None)

    sent = render_transport.headers[0]
    assert "X-Tenant-Id" not in sent, (
        f"{method} is global-by-design and must carry no tenant claim; "
        "if it became tenant-owned, move it to RENDER_TENANT_OWNED"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_kind", "method"),
    [("risk", "calculate_risk"), ("render", "submit_render_package")],
)
async def test_absence_is_sent_as_absence_not_a_substitute(
    client_kind: str,
    method: str,
    risk_transport: _RecordingTransport,
    render_transport: _RecordingTransport,
) -> None:
    """A blank tenant is sent blank, never defaulted to a name.

    The header is present either way, so a regression that stops threading the
    value looks different from a legitimately tenantless call. Substituting a
    default would manufacture an ownership claim indistinguishable from a real
    one — on the render path that claim would ride into archive custody.
    """
    if client_kind == "risk":
        await _risk_client().calculate_risk({"input_mode": "stateful"}, admitted_tenant_id="")
        sent = risk_transport.headers[0]
    else:
        await _render_client().submit_render_package(
            {"render_job_id": "rj-1"},
            correlation_id="corr-1",
            trace_id=None,
            admitted_tenant_id="",
        )
        sent = render_transport.headers[0]

    assert "X-Tenant-Id" in sent, "the header must be present even when the tenant is absent"
    assert sent["X-Tenant-Id"] == ""
    assert sent["X-Tenant-Id"] not in {"lotus-report", "default", "unknown"}


@pytest.mark.asyncio
async def test_risk_propagation_headers_are_not_displaced(
    risk_transport: _RecordingTransport,
) -> None:
    """Adding the tenant must not cost the correlation propagation."""
    await _risk_client().rolling_metrics({"input_mode": "stateful"}, admitted_tenant_id="tenant-sg")

    sent = risk_transport.headers[0]
    assert sent["X-Tenant-Id"] == "tenant-sg"
    assert sent["X-Correlation-Id"] == "corr-prop"


@pytest.mark.asyncio
async def test_render_correlation_and_trace_are_not_displaced(
    render_transport: _RecordingTransport,
) -> None:
    """The render header builder must extend, never replace."""
    await _render_client().get_render_status(
        "rj-1",
        correlation_id="corr-9",
        trace_id="a" * 32,
        admitted_tenant_id="tenant-sg",
    )

    sent = render_transport.headers[0]
    assert sent["X-Tenant-Id"] == "tenant-sg"
    assert sent["X-Correlation-ID"] == "corr-9"
    assert sent["X-Trace-ID"] == "a" * 32
