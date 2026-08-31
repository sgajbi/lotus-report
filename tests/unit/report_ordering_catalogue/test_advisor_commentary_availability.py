"""Mapping pins for the pre-order advisor-commentary availability (issue #166).

Every branch of the lookup-to-availability mapping is pinned, with special
weight on the truthfulness rule: a failed or unverifiable lookup maps to
``advisor_brief_availability_unknown`` and NEVER to ``advisor_brief_not_reviewed``
- claiming "no accepted brief exists" when the lookup merely failed would
assert a fact nobody proved.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.report_ordering_catalogue.advisor_commentary_availability import (
    resolve_advisor_commentary_availability,
)

PORTFOLIO = "PB_SG_GLOBAL_BAL_001"
TENANT = "tenant-sg-001"


class _LookupStub:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self._status_code = status_code
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    async def get_latest_accepted_brief(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        self.calls.append(kwargs)
        return self._status_code, self._payload


class _RaisingLookupStub:
    async def get_latest_accepted_brief(self, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        raise RuntimeError("connection reset")


def _accepted_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": "wfr-accepted-001",
        "content_hash": "a" * 64,
        "context": {
            "portfolio_id": PORTFOLIO,
            "period": "YTD",
            "as_of_date": "2026-04-22",
            "reporting_currency": "USD",
        },
        "review": {"reviewed_by": "banker.sg.301", "reviewed_at": "2026-08-30T09:05:00Z"},
    }
    payload.update(overrides)
    return payload


async def _resolve(client: Any, **overrides: Any) -> Any:
    params: dict[str, Any] = {
        "ai_client": client,
        "portfolio_id": PORTFOLIO,
        "tenant_id": TENANT,
    }
    params.update(overrides)
    return await resolve_advisor_commentary_availability(**params)


@pytest.mark.asyncio
async def test_accepted_lookup_maps_to_ready_with_the_order_identity() -> None:
    client = _LookupStub(200, _accepted_payload())

    availability = await _resolve(client, as_of_date="2026-04-22", reporting_currency="USD")

    assert availability.state == "ready"
    assert availability.reason_code == "advisor_brief_accepted"
    assert availability.accepted_brief is not None
    assert availability.accepted_brief.run_id == "wfr-accepted-001"
    assert availability.accepted_brief.reviewed_by == "banker.sg.301"
    assert availability.accepted_brief.content_hash == "a" * 64
    assert availability.accepted_brief.as_of_date == "2026-04-22"
    assert availability.accepted_brief.reporting_currency == "USD"
    # The lookup was asked with the exact requested scope.
    assert client.calls == [
        {
            "portfolio_id": PORTFOLIO,
            "tenant_id": TENANT,
            "as_of_date": "2026-04-22",
            "reporting_currency": "USD",
        }
    ]


@pytest.mark.asyncio
async def test_no_accepted_run_maps_to_not_reviewed() -> None:
    client = _LookupStub(404, {"metadata": {"reason_code": "no_accepted_run"}})

    availability = await _resolve(client)

    assert availability.state == "unavailable"
    assert availability.reason_code == "advisor_brief_not_reviewed"
    assert availability.accepted_brief is None


@pytest.mark.asyncio
async def test_no_context_match_maps_to_context_mismatch() -> None:
    client = _LookupStub(404, {"metadata": {"reason_code": "no_context_match"}})

    availability = await _resolve(client, as_of_date="2026-05-31")

    assert availability.state == "unavailable"
    assert availability.reason_code == "advisor_brief_context_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,payload",
    [
        (404, {}),
        (404, {"metadata": {"reason_code": "something_new"}}),
        (409, {"metadata": {"reason_code": "lookup_scan_saturated"}}),
        (500, {"detail": "boom"}),
        (200, {}),
        (200, {"run_id": "wfr-1"}),
    ],
)
async def test_unanswerable_lookups_map_to_unknown_never_not_reviewed(
    status_code: int, payload: dict[str, Any]
) -> None:
    availability = await _resolve(_LookupStub(status_code, payload))

    assert availability.state == "unavailable"
    assert availability.reason_code == "advisor_brief_availability_unknown"
    assert "does not mean no accepted brief exists" in availability.message


@pytest.mark.asyncio
async def test_transport_failure_maps_to_unknown() -> None:
    availability = await _resolve(_RaisingLookupStub())

    assert availability.state == "unavailable"
    assert availability.reason_code == "advisor_brief_availability_unknown"


@pytest.mark.asyncio
async def test_identity_echo_mismatch_is_never_ready() -> None:
    """A 200 whose context names a DIFFERENT portfolio must not become ready:
    handing the ordering flow another portfolio's run id would compose the
    wrong client's commentary."""

    payload = _accepted_payload()
    payload["context"]["portfolio_id"] = "PB_SG_OTHER_999"

    availability = await _resolve(_LookupStub(200, payload))

    assert availability.state == "unavailable"
    assert availability.reason_code == "advisor_brief_availability_unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        {"review": None},
        {"review": {"reviewed_by": "", "reviewed_at": "2026-08-30T09:05:00Z"}},
        {"content_hash": ""},
        {"run_id": "  "},
        {"context": "not-a-dict"},
    ],
)
async def test_unverifiable_accepted_payloads_are_never_ready(mutation: dict[str, Any]) -> None:
    availability = await _resolve(_LookupStub(200, _accepted_payload(**mutation)))

    assert availability.state == "unavailable"
    assert availability.reason_code == "advisor_brief_availability_unknown"
