import pytest

from app.clients import ai_client as ai_client_module
from app.clients.ai_client import AiClient


@pytest.mark.asyncio
async def test_get_accepted_workflow_output_calls_projection_endpoint(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_get_with_retry(**kwargs):
        captured.update(kwargs)
        return 200, {"run_id": "run_accept_1", "schema_id": "lotus-ai.x.v1"}

    monkeypatch.setattr(ai_client_module, "get_with_retry", _fake_get_with_retry)
    client = AiClient(
        base_url="http://ai.dev.lotus/",
        timeout_seconds=9.5,
        max_retries=3,
        retry_backoff_seconds=0.5,
    )

    status_code, payload = await client.get_accepted_workflow_output(
        "run_accept_1", tenant_id="tenant-sg"
    )

    assert status_code == 200
    assert payload["run_id"] == "run_accept_1"
    assert captured["url"] == (
        "http://ai.dev.lotus/platform/workflow-packs/runs/run_accept_1/accepted-output"
    )
    headers = captured["headers"]
    assert headers["X-Caller-App"] == "lotus-report"
    assert headers["X-Tenant-Id"] == "tenant-sg"
    assert captured["timeout_seconds"] == 9.5
    assert captured["max_retries"] == 3
    assert captured["backoff_seconds"] == 0.5
