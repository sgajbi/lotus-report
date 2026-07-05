import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

_RETRYABLE_HTTP_STATUSES: set[int] = {429, 502, 503, 504}
_MAX_RETRY_AFTER_SECONDS: float = 5.0
_RequestSender = Callable[[httpx.AsyncClient], Awaitable[httpx.Response]]


def response_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text}
    if isinstance(payload, dict):
        return payload
    return {"detail": payload}


def _retry_after_seconds(response: httpx.Response) -> float | None:
    retry_after = response.headers.get("Retry-After")
    if not retry_after:
        return None
    try:
        value = int(retry_after)
    except ValueError:
        return None
    if value < 0:
        return None
    return min(value, _MAX_RETRY_AFTER_SECONDS)


def _retry_delay_seconds(
    *,
    attempt: int,
    backoff_seconds: float,
    response: httpx.Response | None = None,
) -> float:
    delay: float = max(backoff_seconds, 0.0) * (2**attempt)
    if response is None:
        return delay
    retry_after = _retry_after_seconds(response)
    if retry_after is None:
        return delay
    return max(delay, retry_after)


def _retry_exhausted_payload(
    *,
    status_code: int,
    payload: dict[str, Any],
    attempts: int,
) -> dict[str, Any]:
    enriched = dict(payload)
    enriched["upstream_retry_exhausted"] = True
    enriched["upstream_retry_status_code"] = status_code
    enriched["upstream_retry_attempts"] = attempts
    return enriched


async def _request_with_retry(
    *,
    timeout_seconds: float,
    max_retries: int,
    backoff_seconds: float,
    send_request: _RequestSender,
) -> tuple[int, dict[str, Any]]:
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await send_request(client)
            payload = response_payload(response)
            if response.status_code not in _RETRYABLE_HTTP_STATUSES:
                return response.status_code, payload
            if attempt >= max_retries:
                return response.status_code, _retry_exhausted_payload(
                    status_code=response.status_code,
                    payload=payload,
                    attempts=attempt + 1,
                )
            await asyncio.sleep(
                _retry_delay_seconds(
                    attempt=attempt,
                    backoff_seconds=backoff_seconds,
                    response=response,
                )
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt >= max_retries:
                return 503, {"detail": f"upstream communication failure: {exc.__class__.__name__}"}
            await asyncio.sleep(
                _retry_delay_seconds(attempt=attempt, backoff_seconds=backoff_seconds)
            )
    return 503, {"detail": "upstream communication failure: exhausted retries"}


async def post_with_retry(
    *,
    url: str,
    timeout_seconds: float,
    json_body: dict[str, Any],
    headers: dict[str, str],
    max_retries: int = 2,
    backoff_seconds: float = 0.2,
) -> tuple[int, dict[str, Any]]:
    return await _request_with_retry(
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        backoff_seconds=backoff_seconds,
        send_request=lambda client: client.post(url, json=json_body, headers=headers),
    )


async def get_with_retry(
    *,
    url: str,
    timeout_seconds: float,
    params: dict[str, Any],
    headers: dict[str, str],
    max_retries: int = 2,
    backoff_seconds: float = 0.2,
) -> tuple[int, dict[str, Any]]:
    return await _request_with_retry(
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        backoff_seconds=backoff_seconds,
        send_request=lambda client: client.get(url, params=params, headers=headers),
    )
