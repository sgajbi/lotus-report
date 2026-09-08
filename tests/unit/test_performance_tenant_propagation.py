"""Report sends the admitted tenant to lotus-performance (#361).

Report already sent `X-Tenant-Id` to lotus-ai and lotus-archive, with the same
header and the same value. This client did not, while `admitted_tenant_id` sat
three frames up the call chain -- so every async job Report submitted was
recorded with an absent tenant, and those rows cannot be backfilled: the value
was never transmitted, so it is unattributable in principle rather than merely
un-backfilled.

The assertions are on the **header the transport actually received**, not on the
parameter being passed. A test that checks the argument reached the client proves
the call signature and nothing about the wire, which is the half that matters to
a service building tenant authority over what it stored.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.clients.performance_client import PerformanceClient

ENDPOINTS = {
    "get_workspace_summary": "/performance/workspace-summary",
    "get_contribution": "/performance/contribution",
    "get_attribution": "/performance/attribution",
}


class _RecordingTransport:
    """Captures the headers each POST actually carried."""

    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []

    async def __call__(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        self.headers.append(dict(kwargs["headers"]))
        return 200, {"results_by_period": {}}


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> _RecordingTransport:
    recorder = _RecordingTransport()
    monkeypatch.setattr("app.clients.performance_client.post_with_retry", recorder)
    return recorder


@pytest.mark.asyncio
@pytest.mark.parametrize("method", sorted(ENDPOINTS))
async def test_the_admitted_tenant_reaches_the_wire(
    method: str, transport: _RecordingTransport
) -> None:
    """Every performance call carries the admitted tenant as `X-Tenant-Id`.

    Parameterised over all three rather than the one this was found through:
    the defect was per-method, and fixing only the attribution path would have
    left the same gap on the other two.
    """
    client = PerformanceClient(base_url="http://performance", timeout_seconds=1.0)

    await getattr(client, method)({"portfolio_id": "P1"}, admitted_tenant_id="tenant-sg")

    assert transport.headers, "no request was made"
    assert transport.headers[0].get("X-Tenant-Id") == "tenant-sg"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", sorted(ENDPOINTS))
async def test_an_absent_tenant_sends_absence_and_not_a_substitute(
    method: str, transport: _RecordingTransport
) -> None:
    """Absence is sent as absence, never defaulted.

    lotus-performance reads an absent and a blank header identically, so a
    substituted value would be indistinguishable from a real attribution
    afterwards -- the defect #177 removed from the batch scheduler. The header is
    still present, because omitting it on blank would make a regression that
    stops threading the value look exactly like a legitimately tenantless call.
    """
    client = PerformanceClient(base_url="http://performance", timeout_seconds=1.0)

    await getattr(client, method)({"portfolio_id": "P1"}, admitted_tenant_id="")

    sent = transport.headers[0]
    assert "X-Tenant-Id" in sent, "the header must be present even when the tenant is absent"
    assert sent["X-Tenant-Id"] == ""
    assert sent["X-Tenant-Id"] not in {"default", "lotus-report", "unknown"}, (
        "an absent tenant must not be substituted with a default or a service name"
    )


@pytest.mark.asyncio
async def test_the_tenant_is_carried_on_the_poll_as_well_as_the_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poll is a second request, and it needs the same authority.

    A 202 hands back a `result_path` that the client then polls. Sending the
    tenant on the submit and dropping it on the poll would leave half of every
    async interaction unattributed -- and the poll is the half that reads the
    result.
    """
    posts: list[dict[str, str]] = []
    gets: list[dict[str, str]] = []

    async def _post(**kwargs: Any) -> tuple[int, dict[str, Any]]:
        posts.append(dict(kwargs["headers"]))
        return 202, {"result_path": "/performance/results/abc"}

    class _Response:
        status_code = 200
        headers: dict[str, str] = {}

        def json(self) -> dict[str, Any]:
            return {"results_by_period": {}}

    class _AsyncClient:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> "_AsyncClient":
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def get(self, _url: str, *, params: Any, headers: Any) -> _Response:
            gets.append(dict(headers))
            return _Response()

    monkeypatch.setattr("app.clients.performance_client.post_with_retry", _post)
    # The poll opens httpx directly rather than going through a retry helper, so
    # the transport is substituted at that seam. Checked rather than assumed --
    # the client imports only `post_with_retry`.
    monkeypatch.setattr("app.clients.performance_client.httpx.AsyncClient", _AsyncClient)

    client = PerformanceClient(base_url="http://performance", timeout_seconds=1.0)
    await client.get_attribution({"portfolio_id": "P1"}, admitted_tenant_id="tenant-hk")

    assert posts and posts[0]["X-Tenant-Id"] == "tenant-hk"
    assert gets, "the 202 should have been polled"
    assert gets[0]["X-Tenant-Id"] == "tenant-hk", "the poll dropped the admitted tenant"


@pytest.mark.asyncio
async def test_the_existing_propagation_headers_are_not_displaced(
    transport: _RecordingTransport,
) -> None:
    """Adding the tenant must not cost the correlation and trace headers.

    They are how an operator joins this call to the request that caused it, and
    a merge that replaced the dict rather than extending it would drop them
    silently while every tenant assertion above still passed.
    """
    client = PerformanceClient(base_url="http://performance", timeout_seconds=1.0)

    await client.get_contribution({"portfolio_id": "P1"}, admitted_tenant_id="tenant-sg")

    sent = transport.headers[0]
    for header in ("X-Correlation-Id", "X-Request-Id", "X-Trace-Id"):
        assert header in sent, f"{header} was displaced by the tenant header"
