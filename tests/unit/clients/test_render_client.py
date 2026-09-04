import pytest

from app.clients import render_client as render_client_module
from app.clients.render_client import RenderClient


@pytest.mark.asyncio
async def test_submit_render_package_posts_to_render_endpoint(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_post_with_retry(**kwargs):
        captured.update(kwargs)
        return 201, {"status": "rendered"}

    monkeypatch.setattr(render_client_module, "post_with_retry", _fake_post_with_retry)
    client = RenderClient(
        base_url="http://render.dev.lotus/",
        timeout_seconds=12.5,
        max_retries=4,
        retry_backoff_seconds=0.75,
    )

    status_code, payload = await client.submit_render_package(
        {"render_job_id": "rdr_123"},
        correlation_id="corr-123",
        trace_id="0123456789abcdef0123456789abcdef",
    )

    assert status_code == 201
    assert payload == {"status": "rendered"}
    assert captured == {
        "url": "http://render.dev.lotus/renders",
        "timeout_seconds": 12.5,
        "json_body": {"render_job_id": "rdr_123"},
        "headers": {
            "Content-Type": "application/json",
            "X-Correlation-ID": "corr-123",
            "X-Trace-ID": "0123456789abcdef0123456789abcdef",
            "traceparent": "00-0123456789abcdef0123456789abcdef-0000000000000001-01",
        },
        "max_retries": 4,
        "backoff_seconds": 0.75,
    }


@pytest.mark.asyncio
async def test_submit_render_package_omits_correlation_header_when_absent(monkeypatch):
    captured_headers: dict[str, str] = {}

    async def _fake_post_with_retry(**kwargs):
        captured_headers.update(kwargs["headers"])
        return 200, {}

    monkeypatch.setattr(render_client_module, "post_with_retry", _fake_post_with_retry)
    client = RenderClient(
        base_url="http://render.dev.lotus",
        timeout_seconds=5.0,
        max_retries=1,
        retry_backoff_seconds=0.1,
    )

    await client.submit_render_package({"render_job_id": "rdr_456"})

    assert captured_headers == {"Content-Type": "application/json"}


@pytest.mark.asyncio
async def test_submit_render_package_sends_trace_without_invalid_traceparent(monkeypatch):
    captured_headers: dict[str, str] = {}

    async def _fake_post_with_retry(**kwargs):
        captured_headers.update(kwargs["headers"])
        return 200, {}

    monkeypatch.setattr(render_client_module, "post_with_retry", _fake_post_with_retry)
    client = RenderClient(
        base_url="http://render.dev.lotus",
        timeout_seconds=5.0,
        max_retries=1,
        retry_backoff_seconds=0.1,
    )

    await client.submit_render_package({"render_job_id": "rdr_789"}, trace_id="trace-render")

    assert captured_headers == {
        "Content-Type": "application/json",
        "X-Trace-ID": "trace-render",
    }


@pytest.mark.asyncio
async def test_submit_render_package_omits_traceparent_when_trace_id_is_32_char_invalid_hex(
    monkeypatch,
):
    captured_headers: dict[str, str] = {}

    async def _fake_post_with_retry(**kwargs):
        captured_headers.update(kwargs["headers"])
        return 200, {}

    monkeypatch.setattr(render_client_module, "post_with_retry", _fake_post_with_retry)
    client = RenderClient(
        base_url="http://render.dev.lotus",
        timeout_seconds=5.0,
        max_retries=1,
        retry_backoff_seconds=0.1,
    )

    await client.submit_render_package(
        {"render_job_id": "rdr_790"},
        trace_id="zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
    )

    assert captured_headers == {
        "Content-Type": "application/json",
        "X-Trace-ID": "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
    }


@pytest.mark.asyncio
async def test_get_metadata_uses_resilient_render_metadata_contract(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_get_with_retry(**kwargs):
        captured.update(kwargs)
        return 200, {"supportedOutputFormats": ["pdf"]}

    monkeypatch.setattr(render_client_module, "get_with_retry", _fake_get_with_retry)
    client = RenderClient(
        base_url="http://render.dev.lotus/",
        timeout_seconds=8.0,
        max_retries=3,
        retry_backoff_seconds=0.4,
    )

    status_code, payload = await client.get_metadata(
        correlation_id="corr-catalogue",
        trace_id="0123456789abcdef0123456789abcdef",
    )

    assert status_code == 200
    assert payload == {"supportedOutputFormats": ["pdf"]}
    assert captured == {
        "url": "http://render.dev.lotus/metadata",
        "timeout_seconds": 8.0,
        "params": {},
        "headers": {
            "X-Correlation-ID": "corr-catalogue",
            "X-Trace-ID": "0123456789abcdef0123456789abcdef",
            "traceparent": "00-0123456789abcdef0123456789abcdef-0000000000000001-01",
        },
        "max_retries": 3,
        "backoff_seconds": 0.4,
    }


@pytest.mark.asyncio
async def test_get_metadata_omits_optional_headers_when_context_is_absent(monkeypatch):
    captured_headers: dict[str, str] = {}

    async def _fake_get_with_retry(**kwargs):
        captured_headers.update(kwargs["headers"])
        return 503, {"detail": "render unavailable"}

    monkeypatch.setattr(render_client_module, "get_with_retry", _fake_get_with_retry)
    client = RenderClient(
        base_url="http://render.dev.lotus",
        timeout_seconds=5.0,
        max_retries=1,
        retry_backoff_seconds=0.1,
    )

    status_code, payload = await client.get_metadata()

    assert status_code == 503
    assert payload == {"detail": "render unavailable"}
    assert captured_headers == {}


@pytest.mark.asyncio
async def test_get_template_projection_reads_the_system_templates_surface(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_get_with_retry(**kwargs):
        captured.update(kwargs)
        return 200, {"templates": []}

    monkeypatch.setattr(render_client_module, "get_with_retry", _fake_get_with_retry)
    client = RenderClient(
        base_url="http://render.dev.lotus/",
        timeout_seconds=5.0,
        max_retries=2,
        retry_backoff_seconds=0.1,
    )

    status_code, payload = await client.get_template_projection(
        correlation_id="corr-tpl",
        trace_id="0123456789abcdef0123456789abcdef",
    )

    assert status_code == 200
    assert payload == {"templates": []}
    assert captured["url"] == "http://render.dev.lotus/system/templates"
    assert captured["max_retries"] == 2
