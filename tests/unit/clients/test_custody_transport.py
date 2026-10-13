"""Synthetic HTTP transport controls; no service/network/provider proof."""

import asyncio
import time

import httpx
import pytest

from app.clients.custody_transport import custody_request
from app.reporting_jobs.custody_policy import MAX_RESPONSE_BYTES, CustodyRecoveryError


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **options: original(transport=httpx.MockTransport(handler), **options),
    )


@pytest.mark.asyncio
async def test_one_exact_raw_attempt_and_no_hidden_mutation_retry(monkeypatch):
    sent = []

    def respond(request):
        sent.append(request.content)
        return httpx.Response(503, json={"detail": "unknown"})

    install_transport(monkeypatch, respond)
    body = b'{ "literal": "\xc3\xa9", "spacing" : 1 }'
    result = await custody_request(
        method="POST", url="https://test.invalid/renders", headers={}, body=body, deadline_seconds=1
    )
    assert result == (503, {"detail": "unknown"})
    assert sent == [body]


@pytest.mark.parametrize("kind", ["oversize", "encoding", "headers", "malformed"])
@pytest.mark.asyncio
async def test_response_guards_fail_after_one_unknown_send(monkeypatch, kind):
    calls = []

    def respond(request):
        calls.append(request)
        if kind == "oversize":
            return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
        if kind == "encoding":
            return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=b"")
        if kind == "headers":
            return httpx.Response(200, headers={"X-Large": "x" * 16_385}, json={})
        return httpx.Response(200, content=b"invalid json")

    install_transport(monkeypatch, respond)
    with pytest.raises(ValueError):
        await custody_request(
            method="POST",
            url="https://test.invalid/renders",
            headers={},
            body=b"{}",
            deadline_seconds=1,
        )
    assert len(calls) == 1


class SlowStream(httpx.AsyncByteStream):
    closed = False

    async def __aiter__(self):
        yield b"{"
        await asyncio.sleep(2)
        yield b"}"

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_total_deadline_closes_slow_stream(monkeypatch):
    stream = SlowStream()
    install_transport(monkeypatch, lambda request: httpx.Response(200, stream=stream))
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        await custody_request(
            method="GET",
            url="https://test.invalid/renders/id",
            headers={},
            body=None,
            deadline_seconds=0.03,
        )
    assert stream.closed and time.monotonic() - start < 1


@pytest.mark.parametrize("method,deadline", [("POST", 46), ("GET", 11), ("POST", 0)])
@pytest.mark.asyncio
async def test_deadline_guard_before_network(method, deadline):
    with pytest.raises(CustodyRecoveryError, match="deadline_invalid"):
        await custody_request(
            method=method,
            url="https://test.invalid",
            headers={},
            body=None,
            deadline_seconds=deadline,
        )
