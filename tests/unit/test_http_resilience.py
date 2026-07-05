import json as jsonlib

import httpx
import pytest

from app.clients.http_resilience import get_with_retry, post_with_retry, response_payload


class _FlakyAsyncClient:
    attempts = 0

    def __init__(self, timeout: float):
        _ = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json=None, headers=None):
        payload_json = json
        _ = url, payload_json, headers
        _FlakyAsyncClient.attempts += 1
        if _FlakyAsyncClient.attempts == 1:
            raise httpx.TimeoutException("timeout")
        return httpx.Response(
            status_code=200,
            content=jsonlib.dumps({"ok": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            request=httpx.Request("POST", "http://test"),
        )


class _AlwaysTimeoutAsyncClient:
    def __init__(self, timeout: float):
        _ = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json=None, headers=None):
        _ = url, json, headers
        raise httpx.TimeoutException("timeout")

    async def get(self, url: str, params=None, headers=None):
        _ = url, params, headers
        raise httpx.NetworkError("network")


class _SequenceAsyncClient:
    attempts = 0
    outcomes: list[httpx.Response | BaseException] = []

    def __init__(self, timeout: float):
        _ = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json=None, headers=None):
        _ = url, json, headers
        return self._next()

    async def get(self, url: str, params=None, headers=None):
        _ = url, params, headers
        return self._next()

    @classmethod
    def _next(cls):
        cls.attempts += 1
        outcome = cls.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _json_response(status_code: int, payload: dict, headers: dict[str, str] | None = None):
    return httpx.Response(
        status_code=status_code,
        content=jsonlib.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        request=httpx.Request("POST", "http://test"),
    )


@pytest.mark.asyncio
async def test_post_with_retry_retries_timeout(monkeypatch):
    _FlakyAsyncClient.attempts = 0
    monkeypatch.setattr("httpx.AsyncClient", _FlakyAsyncClient)

    status, payload = await post_with_retry(
        url="http://performances/portfolios/P1/review",
        timeout_seconds=1.0,
        json_body={"as_of_date": "2026-02-25"},
        headers={},
        max_retries=2,
        backoff_seconds=0.0,
    )

    assert status == 200
    assert payload == {"ok": True}
    assert _FlakyAsyncClient.attempts == 2


@pytest.mark.asyncio
async def test_post_with_retry_returns_503_after_retry_exhaustion(monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient", _AlwaysTimeoutAsyncClient)
    status, payload = await post_with_retry(
        url="http://performances/portfolios/P1/review",
        timeout_seconds=1.0,
        json_body={"as_of_date": "2026-02-25"},
        headers={},
        max_retries=0,
        backoff_seconds=0.0,
    )
    assert status == 503
    assert "TimeoutException" in payload["detail"]


@pytest.mark.asyncio
async def test_post_with_retry_hits_exhausted_retries_fallback(monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient", _AlwaysTimeoutAsyncClient)
    status, payload = await post_with_retry(
        url="http://performances/portfolios/P1/review",
        timeout_seconds=1.0,
        json_body={"as_of_date": "2026-02-25"},
        headers={},
        max_retries=-1,
        backoff_seconds=0.0,
    )
    assert status == 503
    assert payload["detail"] == "upstream communication failure: exhausted retries"


@pytest.mark.asyncio
async def test_get_with_retry_returns_503_after_retry_exhaustion(monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient", _AlwaysTimeoutAsyncClient)
    status, payload = await get_with_retry(
        url="http://performances/portfolios/P1/transactions",
        timeout_seconds=1.0,
        params={"limit": 500},
        headers={},
        max_retries=1,
        backoff_seconds=0.0,
    )
    assert status == 503
    assert "NetworkError" in payload["detail"]


@pytest.mark.asyncio
async def test_post_with_retry_retries_transient_http_status(monkeypatch):
    _SequenceAsyncClient.attempts = 0
    _SequenceAsyncClient.outcomes = [
        _json_response(503, {"detail": "temporarily unavailable"}),
        _json_response(200, {"ok": True}),
    ]
    monkeypatch.setattr("httpx.AsyncClient", _SequenceAsyncClient)

    status, payload = await post_with_retry(
        url="http://performances/portfolios/P1/review",
        timeout_seconds=1.0,
        json_body={"as_of_date": "2026-02-25"},
        headers={},
        max_retries=1,
        backoff_seconds=0.0,
    )

    assert status == 200
    assert payload == {"ok": True}
    assert _SequenceAsyncClient.attempts == 2


@pytest.mark.asyncio
async def test_get_with_retry_retries_transient_http_status(monkeypatch):
    _SequenceAsyncClient.attempts = 0
    _SequenceAsyncClient.outcomes = [
        _json_response(429, {"detail": "rate limited"}),
        _json_response(200, {"items": []}),
    ]
    monkeypatch.setattr("httpx.AsyncClient", _SequenceAsyncClient)

    status, payload = await get_with_retry(
        url="http://core/portfolios/P1/transactions",
        timeout_seconds=1.0,
        params={"limit": 500},
        headers={},
        max_retries=1,
        backoff_seconds=0.0,
    )

    assert status == 200
    assert payload == {"items": []}
    assert _SequenceAsyncClient.attempts == 2


@pytest.mark.asyncio
async def test_get_with_retry_reports_exhausted_transient_status(monkeypatch):
    _SequenceAsyncClient.attempts = 0
    _SequenceAsyncClient.outcomes = [
        _json_response(502, {"detail": "bad gateway"}),
        _json_response(504, {"detail": "timeout"}),
    ]
    monkeypatch.setattr("httpx.AsyncClient", _SequenceAsyncClient)

    status, payload = await get_with_retry(
        url="http://core/portfolios/P1/transactions",
        timeout_seconds=1.0,
        params={"limit": 500},
        headers={},
        max_retries=1,
        backoff_seconds=0.0,
    )

    assert status == 504
    assert payload["detail"] == "timeout"
    assert payload["upstream_retry_exhausted"] is True
    assert payload["upstream_retry_status_code"] == 504
    assert payload["upstream_retry_attempts"] == 2


@pytest.mark.asyncio
async def test_post_with_retry_does_not_retry_non_transient_status(monkeypatch):
    _SequenceAsyncClient.attempts = 0
    _SequenceAsyncClient.outcomes = [_json_response(422, {"detail": "invalid request"})]
    monkeypatch.setattr("httpx.AsyncClient", _SequenceAsyncClient)

    status, payload = await post_with_retry(
        url="http://performances/portfolios/P1/review",
        timeout_seconds=1.0,
        json_body={"as_of_date": "bad"},
        headers={},
        max_retries=3,
        backoff_seconds=0.0,
    )

    assert status == 422
    assert payload == {"detail": "invalid request"}
    assert _SequenceAsyncClient.attempts == 1


@pytest.mark.asyncio
async def test_retryable_status_respects_bounded_retry_after(monkeypatch):
    sleep_delays: list[float] = []

    async def _record_sleep(delay: float):
        sleep_delays.append(delay)

    _SequenceAsyncClient.attempts = 0
    _SequenceAsyncClient.outcomes = [
        _json_response(429, {"detail": "rate limited"}, headers={"Retry-After": "10"}),
        _json_response(200, {"ok": True}),
    ]
    monkeypatch.setattr("httpx.AsyncClient", _SequenceAsyncClient)
    monkeypatch.setattr("asyncio.sleep", _record_sleep)

    status, payload = await post_with_retry(
        url="http://archive/reports",
        timeout_seconds=1.0,
        json_body={},
        headers={},
        max_retries=1,
        backoff_seconds=0.2,
    )

    assert status == 200
    assert payload == {"ok": True}
    assert sleep_delays == [5.0]


@pytest.mark.asyncio
async def test_get_with_retry_hits_exhausted_retries_fallback(monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient", _AlwaysTimeoutAsyncClient)
    status, payload = await get_with_retry(
        url="http://performances/portfolios/P1/transactions",
        timeout_seconds=1.0,
        params={"limit": 500},
        headers={},
        max_retries=-1,
        backoff_seconds=0.0,
    )
    assert status == 503
    assert payload["detail"] == "upstream communication failure: exhausted retries"


def test_response_payload_maps_non_dict_and_text_fallback():
    non_dict = httpx.Response(
        status_code=200,
        content=jsonlib.dumps(["value"]).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        request=httpx.Request("POST", "http://test"),
    )
    assert response_payload(non_dict) == {"detail": ["value"]}

    non_json = httpx.Response(
        status_code=502,
        content=b"bad upstream",
        headers={"Content-Type": "text/plain"},
        request=httpx.Request("POST", "http://test"),
    )
    assert response_payload(non_json) == {"detail": "bad upstream"}
