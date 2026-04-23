import pytest

from app.clients.core_query_client import CoreQueryClient
from app.clients.performance_client import PerformanceClient
from app.clients.risk_client import RiskClient
from app.observability import correlation_id_var, request_id_var, trace_id_var


class _FakeResponse:
    def __init__(self, status_code: int, payload, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

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

    async def __aexit__(self, exc_type, exc, tb):
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

    async def __aexit__(self, exc_type, exc, tb):
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
async def test_performance_client_returns_last_pending_contribution_after_polling_budget(
    monkeypatch,
):
    pending_result = {
        "calculation_id": "calc-1",
        "result_path": "/performance/contribution/results/calc-1",
    }
    recorder = _SequencedRecordingAsyncClient(
        responses=[
            _FakeResponse(status_code=202, payload=pending_result),
            *[_FakeResponse(status_code=200, payload=pending_result) for _ in range(8)],
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

    assert status_code == 200
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
