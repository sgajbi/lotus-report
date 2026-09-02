import pytest

from app.clients.core_query_client import CoreQueryClient
from app.clients.performance_client import PerformanceClient
from app.clients.risk_client import RiskClient
from app.observability import correlation_id_var, request_id_var, trace_id_var


class _FakeResponse:
    def __init__(self, status_code: int, payload, text: str = "", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        # A real httpx.Response always has headers; a fake without them is
        # the fake-fidelity gap that hides header-reading code from tests.
        self.headers = dict(headers or {})

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _RecordingAsyncClient:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, exc, _tb):
        return False

    async def post(self, url: str, json: dict, headers: dict):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.response

    async def get(self, url: str, params: dict, headers: dict):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self.response


class _SequencedRecordingAsyncClient:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = responses
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, exc, _tb):
        return False

    async def post(self, url: str, json: dict, headers: dict):
        self.calls.append({"method": "POST", "url": url, "json": json, "headers": headers})
        return self.responses.pop(0)

    async def get(self, url: str, params: dict, headers: dict):
        self.calls.append({"method": "GET", "url": url, "params": params, "headers": headers})
        return self.responses.pop(0)


@pytest.mark.parametrize(
    ("payload", "text", "expected"),
    [
        ({"ok": True}, "", {"ok": True}),
        (["not", "dict"], "", {"detail": ["not", "dict"]}),
        (ValueError("bad json"), "raw-text", {"detail": "raw-text"}),
    ],
)
def test_performance_client_parse_payload(payload, text, expected):
    client = PerformanceClient(base_url="http://performance", timeout_seconds=2.0)
    response = _FakeResponse(status_code=200, payload=payload, text=text)
    assert client._parse_payload(response) == expected


@pytest.mark.asyncio
async def test_performance_client_get_workspace_summary_posts_expected_contract(monkeypatch):
    correlation_id_var.set("corr-1")
    request_id_var.set("req-1")
    trace_id_var.set("0123456789abcdef0123456789abcdef")

    response = _FakeResponse(status_code=200, payload={"results_by_period": {}})
    recorder = _RecordingAsyncClient(response=response)
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )

    client = PerformanceClient(base_url="http://performance/", timeout_seconds=3.0)
    status_code, payload = await client.get_workspace_summary(
        {"portfolio_id": "P1", "report_end_date": "2026-02-24", "periods": []}
    )
    assert status_code == 200
    assert payload == {"results_by_period": {}}
    assert recorder.calls[0]["url"] == "http://performance/performance/workspace-summary"
    assert recorder.calls[0]["json"]["portfolio_id"] == "P1"
    assert recorder.calls[0]["headers"]["X-Correlation-Id"] == "corr-1"


@pytest.mark.asyncio
async def test_performance_client_polls_async_workspace_summary_result(monkeypatch):
    recorder = _SequencedRecordingAsyncClient(
        responses=[
            _FakeResponse(
                status_code=200,
                payload={
                    "calculation_id": "calc-1",
                    "result_path": "/performance/workspace-summary/results/calc-1",
                },
            ),
            _FakeResponse(
                status_code=200,
                payload={
                    "calculation_id": "calc-1",
                    "result_path": "/performance/workspace-summary/results/calc-1",
                },
            ),
            _FakeResponse(status_code=200, payload={"results_by_period": {"YTD": {}}}),
        ]
    )
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.clients.performance_client.asyncio.sleep", _no_sleep)

    client = PerformanceClient(
        base_url="http://performance/",
        timeout_seconds=3.0,
        max_retries=0,
        retry_backoff_seconds=0.01,
    )

    status_code, payload = await client.get_workspace_summary(
        {"portfolio_id": "P1", "report_end_date": "2026-02-24", "periods": []}
    )

    assert status_code == 200
    assert payload == {"results_by_period": {"YTD": {}}}
    assert [call["method"] for call in recorder.calls] == ["POST", "GET", "GET"]
    assert recorder.calls[1]["url"] == (
        "http://performance/performance/workspace-summary/results/calc-1"
    )


@pytest.mark.asyncio
async def test_performance_client_returns_pending_workspace_summary_without_result_path(
    monkeypatch,
):
    recorder = _SequencedRecordingAsyncClient(
        responses=[_FakeResponse(status_code=200, payload={"calculation_id": "calc-1"})]
    )
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )

    client = PerformanceClient(
        base_url="http://performance/",
        timeout_seconds=3.0,
        max_retries=0,
        retry_backoff_seconds=0.01,
    )

    status_code, payload = await client.get_workspace_summary(
        {"portfolio_id": "P1", "report_end_date": "2026-02-24", "periods": []}
    )

    assert status_code == 200
    assert payload == {"calculation_id": "calc-1"}
    assert [call["method"] for call in recorder.calls] == ["POST"]


@pytest.mark.asyncio
async def test_performance_client_returns_last_pending_result_after_polling_budget(
    monkeypatch,
):
    pending_result = {
        "calculation_id": "calc-1",
        "result_path": "/performance/workspace-summary/results/calc-1",
    }
    recorder = _SequencedRecordingAsyncClient(
        responses=[
            _FakeResponse(status_code=200, payload=pending_result),
            _FakeResponse(status_code=200, payload=pending_result),
            _FakeResponse(status_code=200, payload=pending_result),
            _FakeResponse(status_code=200, payload=pending_result),
            _FakeResponse(status_code=200, payload=pending_result),
            _FakeResponse(status_code=200, payload=pending_result),
            _FakeResponse(status_code=200, payload=pending_result),
            _FakeResponse(status_code=200, payload=pending_result),
            _FakeResponse(status_code=200, payload=pending_result),
        ]
    )
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.clients.performance_client.asyncio.sleep", _no_sleep)

    client = PerformanceClient(
        base_url="http://performance/",
        timeout_seconds=3.0,
        max_retries=0,
        retry_backoff_seconds=0.01,
    )

    status_code, payload = await client.get_workspace_summary(
        {"portfolio_id": "P1", "report_end_date": "2026-02-24", "periods": []}
    )

    assert status_code == 200
    assert payload == pending_result
    assert [call["method"] for call in recorder.calls] == ["POST", *["GET"] * 8]


@pytest.mark.parametrize(
    ("payload", "text", "expected"),
    [
        ({"snapshot": {}}, "", {"snapshot": {}}),
        ("non-dict", "", {"detail": "non-dict"}),
        (ValueError("bad json"), "raw-payload", {"detail": "raw-payload"}),
    ],
)
def test_core_query_client_parse_payload(payload, text, expected):
    client = CoreQueryClient(base_url="http://performances", timeout_seconds=2.0)
    response = _FakeResponse(status_code=200, payload=payload, text=text)
    assert client._parse_payload(response) == expected


def test_core_query_client_headers_empty_without_correlation_id():
    client = CoreQueryClient(base_url="http://performances", timeout_seconds=2.0)
    assert client._headers(None) == {}


def test_core_query_client_headers_with_correlation_id_uses_propagation_context():
    request_id_var.set("req-2")
    trace_id_var.set("abcdef0123456789abcdef0123456789")
    client = CoreQueryClient(base_url="http://performances", timeout_seconds=2.0)
    headers = client._headers("corr-2")
    assert headers["X-Correlation-Id"] == "corr-2"
    assert headers["X-Request-Id"] == "req-2"


@pytest.mark.asyncio
async def test_core_query_client_get_portfolio_summary_posts_expected_contract(monkeypatch):
    request_id_var.set("req-3")
    trace_id_var.set("abcdef0123456789abcdef0123456789")
    response = _FakeResponse(status_code=200, payload={"scope": {"portfolio_id": "P3"}})
    recorder = _RecordingAsyncClient(response=response)
    monkeypatch.setattr(
        "app.clients.core_query_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )
    client = CoreQueryClient(base_url="http://performances/", timeout_seconds=3.0)
    body = {"as_of_date": "2026-02-24"}
    status_code, payload = await client.get_portfolio_summary(
        portfolio_id="P3",
        payload=body,
        correlation_id="corr-3",
    )
    assert status_code == 200
    assert payload["scope"]["portfolio_id"] == "P3"
    assert recorder.calls[0]["url"] == "http://performances/reporting/portfolio-summary/query"
    assert recorder.calls[0]["json"] == {"as_of_date": "2026-02-24", "portfolio_id": "P3"}
    assert recorder.calls[0]["headers"]["X-Correlation-Id"] == "corr-3"


@pytest.mark.asyncio
async def test_core_query_client_get_asset_allocation_posts_expected_contract(monkeypatch):
    request_id_var.set("req-4")
    trace_id_var.set("abcdef0123456789abcdef0123456789")
    response = _FakeResponse(status_code=200, payload={"scope": {"portfolio_id": "P4"}})
    recorder = _RecordingAsyncClient(response=response)
    monkeypatch.setattr(
        "app.clients.core_query_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )

    client = CoreQueryClient(
        base_url="http://performances",
        timeout_seconds=5.0,
        max_retries=4,
        retry_backoff_seconds=0.3,
    )
    status_code, payload = await client.get_asset_allocation(
        portfolio_id="P4",
        payload={
            "as_of_date": "2026-02-24",
            "dimensions": ["asset_class", "region"],
            "look_through_mode": "prefer_look_through",
        },
        correlation_id="corr-4",
    )
    assert status_code == 200
    assert payload["scope"]["portfolio_id"] == "P4"
    assert recorder.calls[0]["url"] == "http://performances/reporting/asset-allocation/query"
    assert recorder.calls[0]["json"] == {
        "as_of_date": "2026-02-24",
        "dimensions": ["asset_class", "region"],
        "look_through_mode": "prefer_look_through",
        "scope": {"portfolio_id": "P4"},
    }
    assert recorder.calls[0]["headers"]["X-Correlation-Id"] == "corr-4"


@pytest.mark.asyncio
async def test_core_query_client_get_portfolio_transactions_gets_expected_contract(monkeypatch):
    request_id_var.set("req-5")
    trace_id_var.set("abcdef0123456789abcdef0123456789")
    response = _FakeResponse(status_code=200, payload={"transactions": [], "total": 0})
    recorder = _RecordingAsyncClient(response=response)
    monkeypatch.setattr(
        "app.clients.core_query_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )

    client = CoreQueryClient(base_url="http://performances/", timeout_seconds=3.0)
    status_code, payload = await client.get_portfolio_transactions(
        portfolio_id="P5",
        params={
            "start_date": "2026-01-01",
            "end_date": "2026-02-24",
            "reporting_currency": "USD",
            "sort_by": "transaction_date",
        },
        correlation_id="corr-5",
    )
    assert status_code == 200
    assert payload == {"transactions": [], "total": 0}
    assert recorder.calls[0]["url"] == "http://performances/portfolios/P5/transactions"
    assert recorder.calls[0]["params"] == {
        "start_date": "2026-01-01",
        "end_date": "2026-02-24",
        "reporting_currency": "USD",
        "sort_by": "transaction_date",
    }


@pytest.mark.asyncio
async def test_core_query_client_get_portfolio_positions_gets_expected_contract(monkeypatch):
    request_id_var.set("req-5b")
    trace_id_var.set("abcdef0123456789abcdef0123456789")
    response = _FakeResponse(status_code=200, payload={"positions": [], "total": 0})
    recorder = _RecordingAsyncClient(response=response)
    monkeypatch.setattr(
        "app.clients.core_query_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )

    client = CoreQueryClient(base_url="http://performances/", timeout_seconds=3.0)
    status_code, payload = await client.get_portfolio_positions(
        portfolio_id="P5",
        params={"as_of_date": "2026-02-24", "reporting_currency": "USD"},
        correlation_id="corr-5b",
    )
    assert status_code == 200
    assert payload == {"positions": [], "total": 0}
    assert recorder.calls[0]["url"] == "http://performances/portfolios/P5/positions"
    assert recorder.calls[0]["params"] == {
        "as_of_date": "2026-02-24",
        "reporting_currency": "USD",
    }
    assert recorder.calls[0]["headers"]["X-Correlation-Id"] == "corr-5b"


@pytest.mark.asyncio
async def test_core_query_client_get_portfolio_detail_gets_expected_contract(monkeypatch):
    request_id_var.set("req-5c")
    trace_id_var.set("abcdef0123456789abcdef0123456789")
    response = _FakeResponse(status_code=200, payload={"portfolio_id": "P5"})
    recorder = _RecordingAsyncClient(response=response)
    monkeypatch.setattr(
        "app.clients.core_query_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )

    client = CoreQueryClient(base_url="http://performances/", timeout_seconds=3.0)
    status_code, payload = await client.get_portfolio_detail(
        portfolio_id="P5",
        correlation_id="corr-5c",
    )
    assert status_code == 200
    assert payload == {"portfolio_id": "P5"}
    assert recorder.calls[0]["url"] == "http://performances/portfolios/P5"
    assert recorder.calls[0]["params"] == {}
    assert recorder.calls[0]["headers"]["X-Correlation-Id"] == "corr-5c"


@pytest.mark.asyncio
async def test_core_query_client_get_portfolio_review_posts_expected_contract(monkeypatch):
    response = _FakeResponse(status_code=200, payload={"portfolio_id": "P4"})
    recorder = _RecordingAsyncClient(response=response)
    monkeypatch.setattr(
        "app.clients.core_query_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )
    client = CoreQueryClient(base_url="http://performances/", timeout_seconds=3.0)
    status_code, payload = await client.get_portfolio_review(
        portfolio_id="P4",
        payload={"as_of_date": "2026-02-24"},
        correlation_id=None,
    )
    assert status_code == 200
    assert payload["portfolio_id"] == "P4"
    assert recorder.calls[0]["url"] == "http://performances/portfolios/P4/review"
    assert recorder.calls[0]["headers"] == {}


@pytest.mark.asyncio
async def test_performance_client_get_contribution_posts_expected_contract(monkeypatch):
    response = _FakeResponse(
        status_code=200,
        payload={"results_by_period": {"YTD": {"total_contribution": 1.2}}},
    )
    recorder = _RecordingAsyncClient(response=response)
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )

    client = PerformanceClient(base_url="http://performance/", timeout_seconds=3.0)
    status_code, payload = await client.get_contribution(
        {"portfolio_id": "P1", "report_start_date": "2026-01-01"}
    )

    assert status_code == 200
    assert payload == {"results_by_period": {"YTD": {"total_contribution": 1.2}}}
    assert recorder.calls[0]["url"] == "http://performance/performance/contribution"
    assert recorder.calls[0]["json"] == {
        "portfolio_id": "P1",
        "report_start_date": "2026-01-01",
    }


@pytest.mark.asyncio
async def test_performance_client_get_contribution_polls_async_result(monkeypatch):
    recorder = _SequencedRecordingAsyncClient(
        responses=[
            _FakeResponse(
                status_code=202,
                payload={
                    "calculation_id": "calc-1",
                    "result_path": "/performance/contribution/results/calc-1",
                },
            ),
            _FakeResponse(status_code=200, payload={"results_by_period": {"YTD": {}}}),
        ]
    )
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )

    client = PerformanceClient(
        base_url="http://performance/",
        timeout_seconds=3.0,
        retry_backoff_seconds=0,
    )
    status_code, payload = await client.get_contribution({"portfolio_id": "P1"})

    assert status_code == 200
    assert payload == {"results_by_period": {"YTD": {}}}
    assert recorder.calls[1]["url"] == (
        "http://performance/performance/contribution/results/calc-1"
    )


@pytest.mark.asyncio
async def test_performance_client_get_contribution_returns_accepted_without_result_path(
    monkeypatch,
):
    response = _FakeResponse(status_code=202, payload={"calculation_id": "calc-1"})
    recorder = _RecordingAsyncClient(response=response)
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )

    client = PerformanceClient(base_url="http://performance/", timeout_seconds=3.0)
    status_code, payload = await client.get_contribution({"portfolio_id": "P1"})

    assert status_code == 202
    assert payload == {"calculation_id": "calc-1"}


@pytest.mark.asyncio
async def test_performance_client_get_contribution_returns_last_pending_after_poll_budget(
    monkeypatch,
):
    pending_result = {
        "calculation_id": "calc-1",
        "result_path": "/performance/contribution/results/calc-1",
    }
    recorder = _SequencedRecordingAsyncClient(
        responses=[
            _FakeResponse(status_code=202, payload=pending_result),
            *[_FakeResponse(status_code=202, payload=pending_result) for _ in range(8)],
        ]
    )
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient",
        lambda timeout: recorder,
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.clients.performance_client.asyncio.sleep", _no_sleep)

    client = PerformanceClient(
        base_url="http://performance/",
        timeout_seconds=3.0,
        max_retries=0,
        retry_backoff_seconds=0.01,
    )
    status_code, payload = await client.get_contribution({"portfolio_id": "P1"})

    assert status_code == 202
    assert payload == pending_result
    assert [call["method"] for call in recorder.calls] == ["POST", *["GET"] * 8]


@pytest.mark.asyncio
async def test_risk_client_calculate_risk_posts_expected_contract(monkeypatch):
    async def _fake_post_with_retry(**kwargs):
        return 200, {"results": {}}, kwargs

    monkeypatch.setattr("app.clients.risk_client.post_with_retry", _fake_post_with_retry)
    client = RiskClient(base_url="http://risk/", timeout_seconds=3.0)

    status_code, payload, kwargs = await client.calculate_risk({"metrics": ["VAR"]})
    assert status_code == 200
    assert payload == {"results": {}}
    assert kwargs["url"] == "http://risk/analytics/risk/calculate"


@pytest.mark.asyncio
async def test_performance_client_gets_attribution_synchronously_when_offered(monkeypatch):
    """A 200 with results_by_period is the whole answer; no polling."""

    recorder = _SequencedRecordingAsyncClient(
        responses=[
            _FakeResponse(status_code=200, payload={"results_by_period": {"YTD": {}}}),
        ]
    )
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient", lambda timeout: recorder
    )

    client = PerformanceClient(
        base_url="http://performance/",
        timeout_seconds=3.0,
        max_retries=0,
        retry_backoff_seconds=0.01,
    )

    status_code, payload = await client.get_attribution({"portfolio_id": "P1"})

    assert status_code == 200
    assert payload == {"results_by_period": {"YTD": {}}}
    assert [call["method"] for call in recorder.calls] == ["POST"]
    assert recorder.calls[0]["url"] == "http://performance/performance/attribution"


@pytest.mark.asyncio
async def test_performance_client_polls_accepted_attribution_to_its_result(monkeypatch):
    recorder = _SequencedRecordingAsyncClient(
        responses=[
            _FakeResponse(
                status_code=202,
                payload={
                    "calculation_id": "calc-9",
                    "result_path": "/performance/attribution/results/calc-9",
                    "recommended_poll_after_seconds": 1,
                },
            ),
            _FakeResponse(
                status_code=202,
                payload={
                    "calculation_id": "calc-9",
                    "result_path": "/performance/attribution/results/calc-9",
                },
            ),
            _FakeResponse(status_code=200, payload={"results_by_period": {"YTD": {}}}),
        ]
    )
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient", lambda timeout: recorder
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.clients.performance_client.asyncio.sleep", _no_sleep)

    client = PerformanceClient(
        base_url="http://performance/",
        timeout_seconds=3.0,
        max_retries=0,
        retry_backoff_seconds=0.01,
    )

    status_code, payload = await client.get_attribution({"portfolio_id": "P1"})

    assert status_code == 200
    assert payload == {"results_by_period": {"YTD": {}}}
    assert [call["method"] for call in recorder.calls] == ["POST", "GET", "GET"]
    assert recorder.calls[1]["url"] == ("http://performance/performance/attribution/results/calc-9")


@pytest.mark.asyncio
async def test_attribution_exhaustion_returns_the_accepted_envelope(monkeypatch):
    """The load-bearing behaviour for the capture. A result still pending
    after the budget comes back AS the accepted envelope - status 202, no
    results_by_period - which is the capture's evidence for its
    accepted-but-not-complete posture. The client never raises on pending:
    what a pending attribution means for a report is the capture's judgement,
    not the transport's."""

    pending = _FakeResponse(
        status_code=202,
        payload={
            "calculation_id": "calc-9",
            "result_path": "/performance/attribution/results/calc-9",
            "recommended_poll_after_seconds": 1,
        },
    )
    recorder = _SequencedRecordingAsyncClient(responses=[pending] * 20)
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient", lambda timeout: recorder
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.clients.performance_client.asyncio.sleep", _no_sleep)

    client = PerformanceClient(
        base_url="http://performance/",
        timeout_seconds=3.0,
        max_retries=0,
        retry_backoff_seconds=0.01,
    )

    status_code, payload = await client.get_attribution({"portfolio_id": "P1"})

    assert status_code == 202
    assert "results_by_period" not in payload
    assert payload["calculation_id"] == "calc-9"


class _FakeTime:
    """A clock and sleeper for proving budget semantics without wall time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _budget_client(recorder, fake_time, *, poll_budget_seconds):
    return PerformanceClient(
        base_url="http://performance/",
        timeout_seconds=3.0,
        max_retries=0,
        retry_backoff_seconds=0.25,
        poll_budget_seconds=poll_budget_seconds,
        clock=fake_time.clock,
        sleeper=fake_time.sleep,
    )


@pytest.mark.asyncio
async def test_a_stated_wait_within_budget_is_honoured_in_full(monkeypatch):
    """Retry-After is the source's minimum, not a suggestion Report may
    shorten: the loop sleeps exactly the stated wait before polling."""

    recorder = _SequencedRecordingAsyncClient(
        responses=[
            _FakeResponse(
                status_code=202,
                payload={
                    "calculation_id": "calc-9",
                    "result_path": "/performance/attribution/results/calc-9",
                    "recommended_poll_after_seconds": 2,
                },
            ),
            _FakeResponse(status_code=200, payload={"results_by_period": {"YTD": {}}}),
        ]
    )
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient", lambda timeout: recorder
    )
    fake_time = _FakeTime()

    client = _budget_client(recorder, fake_time, poll_budget_seconds=10.0)
    status_code, payload = await client.get_attribution({"portfolio_id": "P1"})

    assert status_code == 200
    assert payload == {"results_by_period": {"YTD": {}}}
    assert fake_time.sleeps == [2.0]


@pytest.mark.asyncio
async def test_a_stated_wait_beyond_the_budget_stops_polling_immediately(monkeypatch):
    """The steering case. The envelope says come back in 60s; Report's budget
    is 10s. Polling early would disobey the source, sleeping on would disobey
    the budget - so the loop performs ZERO polls and ZERO sleeps and returns
    the accepted envelope, which becomes the truthful pending posture."""

    recorder = _SequencedRecordingAsyncClient(
        responses=[
            _FakeResponse(
                status_code=202,
                payload={
                    "calculation_id": "calc-9",
                    "result_path": "/performance/attribution/results/calc-9",
                    "recommended_poll_after_seconds": 60,
                },
            ),
        ]
    )
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient", lambda timeout: recorder
    )
    fake_time = _FakeTime()

    client = _budget_client(recorder, fake_time, poll_budget_seconds=10.0)
    status_code, payload = await client.get_attribution({"portfolio_id": "P1"})

    assert status_code == 202
    assert payload["calculation_id"] == "calc-9"
    assert fake_time.sleeps == []
    assert [call["method"] for call in recorder.calls] == ["POST"]


@pytest.mark.asyncio
async def test_a_long_retry_after_arriving_mid_poll_ends_the_loop_truthfully(monkeypatch):
    """An hour-long Retry-After is obeyed by NOT polling again within the
    budget - never shortened and called early, never slept past the budget.
    The last accepted envelope is returned for the pending posture."""

    recorder = _SequencedRecordingAsyncClient(
        responses=[
            _FakeResponse(
                status_code=202,
                payload={
                    "calculation_id": "calc-9",
                    "result_path": "/performance/attribution/results/calc-9",
                },
            ),
            _FakeResponse(
                status_code=202,
                payload={"calculation_id": "calc-9"},
                headers={"Retry-After": "3600"},
            ),
        ]
    )
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient", lambda timeout: recorder
    )
    fake_time = _FakeTime()

    client = _budget_client(recorder, fake_time, poll_budget_seconds=10.0)
    status_code, payload = await client.get_attribution({"portfolio_id": "P1"})

    assert status_code == 202
    assert payload == {"calculation_id": "calc-9"}
    # One immediate poll (nothing was stated yet) receives the 3600s
    # instruction, which ends the loop: no second GET, no sleep ever recorded.
    assert [call["method"] for call in recorder.calls] == ["POST", "GET"]
    assert fake_time.sleeps == []


@pytest.mark.asyncio
async def test_an_unstated_cadence_never_sleeps_past_the_budget(monkeypatch):
    """A source stating nothing gets the linear fallback, and the budget still
    bounds the total: the loop ends with the accepted envelope rather than
    oversleeping, proven on the fake clock."""

    pending = _FakeResponse(
        status_code=202,
        payload={
            "calculation_id": "calc-9",
            "result_path": "/performance/attribution/results/calc-9",
        },
    )
    recorder = _SequencedRecordingAsyncClient(responses=[pending] * 20)
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient", lambda timeout: recorder
    )
    fake_time = _FakeTime()

    client = _budget_client(recorder, fake_time, poll_budget_seconds=1.0)
    status_code, payload = await client.get_attribution({"portfolio_id": "P1"})

    assert status_code == 202
    assert "results_by_period" not in payload
    assert fake_time.now <= 1.0
    assert sum(fake_time.sleeps) <= 1.0


@pytest.mark.asyncio
async def test_an_accepted_attribution_without_a_result_path_is_returned_as_is(monkeypatch):
    """A 202 that names no result_path cannot be polled; the envelope goes
    back to the capture unchanged rather than being retried against nothing."""

    recorder = _SequencedRecordingAsyncClient(
        responses=[_FakeResponse(status_code=202, payload={"calculation_id": "calc-9"})]
    )
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient", lambda timeout: recorder
    )

    client = PerformanceClient(
        base_url="http://performance/",
        timeout_seconds=3.0,
        max_retries=0,
        retry_backoff_seconds=0.01,
    )

    status_code, payload = await client.get_attribution({"portfolio_id": "P1"})

    assert status_code == 202
    assert payload == {"calculation_id": "calc-9"}
    assert [call["method"] for call in recorder.calls] == ["POST"]


@pytest.mark.asyncio
async def test_a_malformed_retry_after_header_falls_back_rather_than_crashing(monkeypatch):
    """Retry-After is delta-seconds by contract, but a header is still input:
    a value that does not parse is ignored and the linear fallback applies,
    because one bad header must not fail a poll that would have succeeded."""

    recorder = _SequencedRecordingAsyncClient(
        responses=[
            _FakeResponse(
                status_code=202,
                payload={
                    "calculation_id": "calc-9",
                    "result_path": "/performance/attribution/results/calc-9",
                },
            ),
            _FakeResponse(
                status_code=202,
                payload={"calculation_id": "calc-9"},
                headers={"Retry-After": "soon"},
            ),
            _FakeResponse(status_code=200, payload={"results_by_period": {"YTD": {}}}),
        ]
    )
    monkeypatch.setattr(
        "app.clients.performance_client.httpx.AsyncClient", lambda timeout: recorder
    )

    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.clients.performance_client.asyncio.sleep", _record_sleep)

    client = PerformanceClient(
        base_url="http://performance/",
        timeout_seconds=3.0,
        max_retries=0,
        retry_backoff_seconds=0.25,
    )

    status_code, _payload = await client.get_attribution({"portfolio_id": "P1"})

    assert status_code == 200
    # The unparseable header fell back to the linear schedule (0.25 * 1).
    assert sleeps[-1] == 0.25
