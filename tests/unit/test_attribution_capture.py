"""The first async capture states what it could establish (issue #254)."""

import pytest

from app.services.attribution_capture import (
    attribution_calculation_id,
    build_attribution_request,
    capture_attribution,
)


class _AttributionClient:
    def __init__(self, status_code, payload):
        self._status_code = status_code
        self._payload = payload
        self.requests = []

    async def get_attribution(self, payload):
        self.requests.append(payload)
        return self._status_code, self._payload


class _DownClient:
    async def get_attribution(self, payload):
        raise RuntimeError("connection refused")


RESULTS = {
    "results_by_period": {
        "YTD": {
            "status": "ok",
            "levels": [{"dimension": "asset_class", "groups": []}],
            "reconciliation": {
                "total_active_return": 0.42,
                "sum_of_effects": 0.41,
                "residual": 0.01,
            },
        }
    },
    "model": "brinson_fachler",
    "linking": "carino",
    "benchmark_context": {"benchmark_id": "BMK", "return_source": "calculated"},
    "calculation_supportability": {"state": "ready"},
}


async def _capture(client):
    return await capture_attribution(
        performance_client=client,
        portfolio_id="P1",
        as_of_date="2026-04-22",
        benchmark_code="BMK",
    )


@pytest.mark.asyncio
async def test_results_are_captured_verbatim_with_their_source_identity():
    """The decomposition, the source-classified residual, and the model
    identity are lotus-performance's statements. Report never rebalances a
    residual or reweights an effect - what arrived is what is stored."""

    client = _AttributionClient(200, RESULTS)

    section = await _capture(client)

    assert section["status"] == "present"
    assert section["results_by_period"] == RESULTS["results_by_period"]
    assert section["model"] == "brinson_fachler"
    assert section["linking"] == "carino"
    assert section["source"] == {
        "service": "lotus-performance",
        "endpoint": "/performance/attribution",
    }
    assert section["supportability"] == {"status": "ready", "notes": []}


@pytest.mark.asyncio
async def test_accepted_but_not_complete_is_a_stated_posture_not_a_failure():
    """The new ground. A 202 still standing after the client's poll budget
    means the source accepted the work and had not finished it - not absence,
    not an error. The section says `pending` with the calculation identity, so
    a regenerate collects the finished result instead of guessing."""

    client = _AttributionClient(
        202,
        {
            "calculation_id": "calc-9",
            "result_path": "/performance/attribution/results/calc-9",
        },
    )

    section = await _capture(client)

    assert section["status"] == "pending"
    assert section["accepted"]["calculation_id"] == "calc-9"
    assert section["accepted"]["result_path"] == "/performance/attribution/results/calc-9"
    assert section["supportability"]["notes"][0]["code"] == "attribution_accepted_not_complete"


@pytest.mark.asyncio
async def test_an_identical_retry_converges_on_the_same_upstream_calculation():
    """The retry-convergence rule applied to an async source. The calculation
    id is derived from what is asked, so a capture retry re-addresses the SAME
    upstream calculation instead of submitting a new one - and a different
    question gets a different id rather than colliding with an old answer."""

    client = _AttributionClient(200, RESULTS)
    await _capture(client)
    await _capture(client)

    first, second = client.requests
    assert first["calculation_id"] == second["calculation_id"]

    different_day = attribution_calculation_id(portfolio_id="P1", as_of_date="2026-04-23")
    different_portfolio = attribution_calculation_id(portfolio_id="P2", as_of_date="2026-04-22")
    assert len({first["calculation_id"], different_day, different_portfolio}) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (409, "attribution_execution_failed"),
        (422, "attribution_unsupported_for_portfolio"),
        (400, "attribution_source_refused"),
    ],
)
async def test_refusals_carry_a_bounded_reason_never_a_guess(status_code, expected_code):
    """409 is a failed execution (re-order is the remedy); 422 is a fact about
    the mandate's inputs; anything else is a refusal Report does not recognise
    and says so - the same honest fall-through as the commentary mapping."""

    client = _AttributionClient(status_code, {"detail": "why"})

    section = await _capture(client)

    assert section["status"] == "unavailable"
    assert section["supportability"]["notes"][0]["code"] == expected_code
    assert section["supportability"]["notes"][0]["message"] == "why"


@pytest.mark.asyncio
async def test_transport_failure_closes_the_section_not_the_report():
    """Attribution is optional; denying a client a report because one
    decomposition's source was unreachable would invert the section-vs-job
    split."""

    section = await _capture(_DownClient())

    assert section["status"] == "unavailable"
    assert section["supportability"]["notes"][0]["code"] == "attribution_upstream_failure"


def test_the_request_asks_for_the_agreed_contract():
    """Stateful Brinson, NET, asset_class, YTD window from January 1st of the
    as-of year - the #254 contract, and the report's resolved benchmark rather
    than leaving lotus-core's assignment to answer a different question."""

    request = build_attribution_request(
        portfolio_id="P1", as_of_date="2026-04-22", benchmark_code="BMK"
    )

    assert request["input_mode"] == "stateful"
    assert request["group_by"] == ["asset_class"]
    assert request["report_start_date"] == "2026-01-01"
    assert request["report_end_date"] == "2026-04-22"
    assert request["analyses"] == [{"period": "YTD", "frequencies": ["daily"]}]
    assert request["stateful_input"]["metric_basis"] == "NET"
    assert request["stateful_input"]["benchmark_id"] == "BMK"
