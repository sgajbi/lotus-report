"""RiskClient.rolling_metrics: the one upstream call behind the risk trend."""

import pytest

from app.clients import risk_client as risk_client_module
from app.clients.risk_client import RiskClient


@pytest.mark.asyncio
async def test_rolling_metrics_posts_to_the_rolling_metrics_endpoint(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_post_with_retry(**kwargs):
        captured.update(kwargs)
        return 200, {"results": {}}

    monkeypatch.setattr(risk_client_module, "post_with_retry", _fake_post_with_retry)
    monkeypatch.setattr(
        risk_client_module, "propagation_headers", lambda: {"X-Correlation-ID": "corr-9"}
    )
    client = RiskClient(
        base_url="http://risk.dev.lotus/",
        timeout_seconds=12.5,
        max_retries=4,
        retry_backoff_seconds=0.75,
    )

    status_code, payload = await client.rolling_metrics({"input_mode": "stateful"})

    assert status_code == 200
    assert payload == {"results": {}}
    assert captured == {
        "url": "http://risk.dev.lotus/analytics/risk/rolling-metrics",
        "timeout_seconds": 12.5,
        "json_body": {"input_mode": "stateful"},
        "headers": {"X-Correlation-ID": "corr-9"},
        "max_retries": 4,
        "backoff_seconds": 0.75,
    }
