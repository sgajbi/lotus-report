import json as jsonlib

import httpx
import pytest

from app.clients.http_resilience import post_with_retry


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


@pytest.mark.asyncio
async def test_post_with_retry_retries_timeout(monkeypatch):
    _FlakyAsyncClient.attempts = 0
    monkeypatch.setattr("httpx.AsyncClient", _FlakyAsyncClient)

    status, payload = await post_with_retry(
        url="http://pas/portfolios/P1/review",
        timeout_seconds=1.0,
        json_body={"as_of_date": "2026-02-25"},
        headers={},
        max_retries=2,
        backoff_seconds=0.0,
    )

    assert status == 200
    assert payload == {"ok": True}
    assert _FlakyAsyncClient.attempts == 2
