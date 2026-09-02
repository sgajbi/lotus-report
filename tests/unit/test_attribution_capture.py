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
    """The retry-convergence rule applied to an async source, against
    lotus-performance's VERIFIED idempotency: a duplicate id with a matching
    replay signature returns the existing execution, and a duplicate id with
    a different payload is a 409 CONFLICT. Convergence therefore requires the
    id to be identical exactly when the payload is."""

    client = _AttributionClient(200, RESULTS)
    await _capture(client)
    await _capture(client)

    first, second = client.requests
    assert first == second
    assert first["calculation_id"] == second["calculation_id"]


def _request(**overrides):
    request = build_attribution_request(
        portfolio_id=overrides.pop("portfolio_id", "P1"),
        as_of_date=overrides.pop("as_of_date", "2026-04-22"),
        benchmark_code=overrides.pop("benchmark_code", "BMK_A"),
    )
    request.pop("calculation_id")
    request.update(overrides)
    return request


def test_the_identity_binds_the_complete_financial_question():
    """Same financial question -> same identity; different financial question
    -> different identity. The id is derived from the canonical request body,
    so EVERY input capable of changing the authoritative result changes it -
    the previous hand-picked tuple omitted the benchmark, and the same id
    with a different benchmark would have collided with the old calculation
    as the source's 409 CONFLICT."""

    base = attribution_calculation_id(_request())

    assert attribution_calculation_id(_request()) == base

    different_benchmark = _request()
    different_benchmark["stateful_input"]["benchmark_id"] = "BMK_B"
    assert attribution_calculation_id(different_benchmark) != base

    different_grouping = _request(group_by=["sector"])
    assert attribution_calculation_id(different_grouping) != base

    different_window = _request(report_end_date="2026-04-23")
    assert attribution_calculation_id(different_window) != base

    different_period = _request(analyses=[{"period": "1Y", "frequencies": ["daily"]}])
    assert attribution_calculation_id(different_period) != base

    different_basis = _request()
    different_basis["stateful_input"]["metric_basis"] = "GROSS"
    assert attribution_calculation_id(different_basis) != base

    every_id = {
        base,
        attribution_calculation_id(different_benchmark),
        attribution_calculation_id(different_grouping),
        attribution_calculation_id(different_window),
        attribution_calculation_id(different_period),
        attribution_calculation_id(different_basis),
    }
    assert len(every_id) == 6


def test_transport_metadata_never_changes_the_identity():
    """Correlation ids, retry counts and timestamps are transport, not the
    financial question. They never enter the request body (correlation
    travels in headers), and the id key itself is excluded - so a request
    carrying a stale calculation_id still derives the same identity from what
    it asks."""

    with_stale_id = _request()
    with_stale_id["calculation_id"] = "something-old"

    assert attribution_calculation_id(with_stale_id) == attribution_calculation_id(_request())


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
