"""One cancellable HTTP attempt with an elapsed deadline and decoded response cap."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.reporting_jobs.custody_policy import MAX_RESPONSE_BYTES, CustodyRecoveryError


async def custody_request(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    deadline_seconds: float,
) -> tuple[int, dict[str, Any]]:
    if not 0 < deadline_seconds <= (45 if method == "POST" else 10):
        raise CustodyRecoveryError("custody_transport_deadline_invalid")
    # The deadline includes async-client/stream close and cancellation; no retry thread.
    async with asyncio.timeout(deadline_seconds):
        async with httpx.AsyncClient(timeout=deadline_seconds, follow_redirects=False) as client:
            async with client.stream(
                method,
                url,
                content=body,
                headers={**headers, "Accept-Encoding": "identity"},
            ) as response:
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise CustodyRecoveryError("custody_response_encoding_unsupported")
                if sum(len(k) + len(v) for k, v in response.headers.items()) > 16_384:
                    raise CustodyRecoveryError("custody_response_headers_exceeded")
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=16_384):
                    if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise CustodyRecoveryError("custody_response_size_exceeded")
                    data.extend(chunk)
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    raise CustodyRecoveryError("custody_response_shape_invalid")
                return response.status_code, payload
