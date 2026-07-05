import json

import pytest
from fastapi import Request

from app.enterprise_readiness import (
    authorize_read_request,
    authorize_write_request,
    build_enterprise_audit_middleware,
    enterprise_runtime_profile,
    is_feature_enabled,
    redact_sensitive,
    validate_enterprise_runtime_config,
)


def _request(
    scope: dict,
    *,
    body: bytes = b"",
    chunks: list[bytes] | None = None,
) -> Request:
    body_chunks = chunks if chunks is not None else [body]
    messages = [{"type": "http.request", "body": chunk, "more_body": True} for chunk in body_chunks]
    if messages:
        messages[-1]["more_body"] = False
    else:
        messages.append({"type": "http.request", "body": b"", "more_body": False})

    async def receive():
        if messages:
            return messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def test_feature_flags_resolution(monkeypatch):
    monkeypatch.setenv(
        "ENTERPRISE_FEATURE_FLAGS_JSON",
        json.dumps({"reports.export": {"tenant-r": {"ops": True, "*": False}}}),
    )
    assert is_feature_enabled("reports.export", "tenant-r", "ops") is True
    assert is_feature_enabled("reports.export", "tenant-r", "advisor") is False


def test_redaction_masks_sensitive_values():
    payload = {"token": "x", "nested": {"account_number": "1", "safe": "ok"}}
    redacted = redact_sensitive(payload)
    assert redacted["token"] == "***REDACTED***"
    assert redacted["nested"]["account_number"] == "***REDACTED***"
    assert redacted["nested"]["safe"] == "ok"


def test_authorize_write_request_enforces_required_headers_when_enabled(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "true")
    allowed, reason = authorize_write_request("POST", "/reports/portfolios/P1/review", {})
    assert allowed is False
    assert reason.startswith("missing_headers:")


def test_authorize_write_request_enforces_capability_rules(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "true")
    monkeypatch.setenv(
        "ENTERPRISE_CAPABILITY_RULES_JSON",
        json.dumps({"POST /reports/portfolios/": "reports.write"}),
    )
    headers = {
        "X-Actor-Id": "a1",
        "X-Tenant-Id": "t1",
        "X-Role": "ops",
        "X-Correlation-Id": "c1",
        "X-Service-Identity": "ras",
        "X-Capabilities": "reports.read",
    }
    denied, denied_reason = authorize_write_request(
        "POST", "/reports/portfolios/P1/review", headers
    )
    assert denied is False
    assert denied_reason == "missing_capability:reports.write"

    headers["X-Capabilities"] = "reports.read,reports.write"
    allowed, allowed_reason = authorize_write_request(
        "POST", "/reports/portfolios/P1/review", headers
    )
    assert allowed is True
    assert allowed_reason is None


def test_validate_enterprise_runtime_config_reports_rotation_issue(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_SECRET_ROTATION_DAYS", "120")
    issues = validate_enterprise_runtime_config()
    assert "secret_rotation_days_out_of_range" in issues


def test_invalid_json_and_invalid_int_env_defaults(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_FEATURE_FLAGS_JSON", "{bad")
    monkeypatch.setenv("ENTERPRISE_SECRET_ROTATION_DAYS", "not-a-number")
    assert is_feature_enabled("reports.export", "tenant-r", "ops") is False
    issues = validate_enterprise_runtime_config()
    assert "secret_rotation_days_out_of_range" not in issues


def test_validate_runtime_config_flags_missing_policy_and_key(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_POLICY_VERSION", " ")
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "true")
    monkeypatch.delenv("ENTERPRISE_PRIMARY_KEY_ID", raising=False)
    issues = validate_enterprise_runtime_config()
    assert "missing_policy_version" in issues
    assert "missing_primary_key_id" in issues


def test_validate_runtime_config_requires_primary_key_for_read_auth(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_READ_AUTHZ", "true")
    monkeypatch.delenv("ENTERPRISE_PRIMARY_KEY_ID", raising=False)
    issues = validate_enterprise_runtime_config()
    assert "missing_primary_key_id" in issues


def test_validate_runtime_config_keeps_explicit_local_debug_profile_permissive(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_RUNTIME_PROFILE", "local")
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "false")
    monkeypatch.setenv("ENTERPRISE_ENFORCE_READ_AUTHZ", "false")
    monkeypatch.delenv("ENTERPRISE_PRIMARY_KEY_ID", raising=False)

    issues = validate_enterprise_runtime_config()

    assert enterprise_runtime_profile() == "local"
    assert "production_write_authz_not_enabled" not in issues
    assert "production_read_authz_not_enabled" not in issues
    assert "missing_primary_key_id" not in issues


def test_validate_runtime_config_fails_closed_for_production_profile(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_RUNTIME_PROFILE", "production")
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "false")
    monkeypatch.setenv("ENTERPRISE_ENFORCE_READ_AUTHZ", "false")
    monkeypatch.delenv("ENTERPRISE_PRIMARY_KEY_ID", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        validate_enterprise_runtime_config()

    message = str(exc_info.value)
    assert "production_write_authz_not_enabled" in message
    assert "production_read_authz_not_enabled" in message
    assert "missing_primary_key_id" in message


def test_validate_runtime_config_accepts_production_profile_with_authz_and_key(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_RUNTIME_PROFILE", "production")
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "true")
    monkeypatch.setenv("ENTERPRISE_ENFORCE_READ_AUTHZ", "true")
    monkeypatch.setenv("ENTERPRISE_PRIMARY_KEY_ID", "primary-2026-07")

    assert validate_enterprise_runtime_config() == []


def test_authorize_write_request_fails_closed_in_production_profile(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_RUNTIME_PROFILE", "production")
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "false")

    allowed, reason = authorize_write_request("POST", "/reports/portfolios/P1/review", {})

    assert allowed is False
    assert reason.startswith("missing_headers:")


def test_authorize_read_request_fails_closed_in_production_profile(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_RUNTIME_PROFILE", "production")
    monkeypatch.setenv("ENTERPRISE_ENFORCE_READ_AUTHZ", "false")

    allowed, reason = authorize_read_request("GET", "/reports/jobs", {})

    assert allowed is False
    assert reason.startswith("missing_headers:")


@pytest.mark.asyncio
async def test_middleware_blocks_oversized_payload(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "false")
    monkeypatch.setenv("ENTERPRISE_MAX_WRITE_PAYLOAD_BYTES", "1")
    middleware = build_enterprise_audit_middleware()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/reports/portfolios/P1/review",
        "headers": [(b"content-length", b"2")],
    }
    request = _request(scope, body=b"xx")
    response = await middleware(request, lambda req: None)  # pragma: no cover
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_middleware_denies_missing_service_identity(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "true")
    middleware = build_enterprise_audit_middleware()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/reports/portfolios/P1/review",
        "headers": [
            (b"x-actor-id", b"a1"),
            (b"x-tenant-id", b"t1"),
            (b"x-role", b"ops"),
            (b"x-correlation-id", b"c1"),
            (b"x-capabilities", b"reports.write"),
        ],
    }
    request = _request(scope)
    response = await middleware(request, lambda req: None)  # pragma: no cover
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_middleware_production_profile_denies_direct_write_without_identity(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_RUNTIME_PROFILE", "production")
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "false")
    monkeypatch.setenv("ENTERPRISE_MAX_WRITE_PAYLOAD_BYTES", "64")
    middleware = build_enterprise_audit_middleware()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/reports/portfolios/P1/review",
        "headers": [
            (b"x-actor-id", b"a1"),
            (b"x-tenant-id", b"t1"),
            (b"x-role", b"ops"),
            (b"x-correlation-id", b"c1"),
            (b"content-length", b"0"),
        ],
    }
    request = _request(scope)
    response = await middleware(request, lambda req: None)  # pragma: no cover
    assert response.status_code == 403
    assert json.loads(response.body)["reason"] == "missing_service_identity"


@pytest.mark.asyncio
async def test_middleware_rejects_invalid_content_length(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "false")
    monkeypatch.setenv("ENTERPRISE_POLICY_VERSION", "2.0.0")
    middleware = build_enterprise_audit_middleware()

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/reports/portfolios/P1/review",
        "headers": [(b"content-length", b"abc")],
    }
    request = _request(scope, body=b"{}")
    response = await middleware(request, lambda req: None)  # pragma: no cover
    assert response.status_code == 400
    assert json.loads(response.body) == {"detail": "invalid_content_length"}


@pytest.mark.asyncio
async def test_middleware_blocks_missing_content_length_oversized_body(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "false")
    monkeypatch.setenv("ENTERPRISE_MAX_WRITE_PAYLOAD_BYTES", "1")
    middleware = build_enterprise_audit_middleware()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/reports/portfolios/P1/review",
        "headers": [],
    }
    request = _request(scope, body=b"xx")
    response = await middleware(request, lambda req: None)  # pragma: no cover
    assert response.status_code == 413
    assert json.loads(response.body) == {"detail": "payload_too_large"}


@pytest.mark.asyncio
async def test_middleware_blocks_streamed_oversized_body(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "false")
    monkeypatch.setenv("ENTERPRISE_MAX_WRITE_PAYLOAD_BYTES", "1")
    middleware = build_enterprise_audit_middleware()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/reports/portfolios/P1/review",
        "headers": [],
    }
    request = _request(scope, chunks=[b"x", b"y"])
    response = await middleware(request, lambda req: None)  # pragma: no cover
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_middleware_rejects_underdeclared_oversized_body(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "false")
    monkeypatch.setenv("ENTERPRISE_MAX_WRITE_PAYLOAD_BYTES", "1")
    middleware = build_enterprise_audit_middleware()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/reports/portfolios/P1/review",
        "headers": [(b"content-length", b"1")],
    }
    request = _request(scope, body=b"xx")
    response = await middleware(request, lambda req: None)  # pragma: no cover
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_middleware_accepts_valid_body_without_content_length(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "false")
    monkeypatch.setenv("ENTERPRISE_MAX_WRITE_PAYLOAD_BYTES", "64")
    monkeypatch.setenv("ENTERPRISE_POLICY_VERSION", "2.0.0")
    middleware = build_enterprise_audit_middleware()
    body = b'{"ok":true}'

    async def _call_next(request: Request):
        from fastapi.responses import JSONResponse

        assert await request.body() == body
        return JSONResponse({"ok": True}, status_code=200)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/reports/portfolios/P1/review",
        "headers": [],
    }
    request = _request(scope, body=body)
    response = await middleware(request, _call_next)
    assert response.status_code == 200
    assert response.headers["X-Enterprise-Policy-Version"] == "2.0.0"


def test_validate_runtime_config_raises_when_enforced(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_POLICY_VERSION", " ")
    monkeypatch.setenv("ENTERPRISE_ENFORCE_RUNTIME_CONFIG", "true")
    with pytest.raises(RuntimeError, match="enterprise_runtime_config_invalid"):
        validate_enterprise_runtime_config()


def test_authorize_write_request_allows_when_rule_not_matching_path(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "true")
    monkeypatch.setenv(
        "ENTERPRISE_CAPABILITY_RULES_JSON", json.dumps({"POST /other": "reports.write"})
    )
    headers = {
        "X-Actor-Id": "a1",
        "X-Tenant-Id": "t1",
        "X-Role": "ops",
        "X-Correlation-Id": "c1",
        "X-Service-Identity": "lotus-report",
    }
    allowed, reason = authorize_write_request("POST", "/reports/export", headers)
    assert allowed is True
    assert reason is None


def test_authorize_read_request_enforces_required_headers_when_enabled(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_READ_AUTHZ", "true")
    allowed, reason = authorize_read_request("GET", "/reports/jobs", {})
    assert allowed is False
    assert reason.startswith("missing_headers:")


def test_authorize_read_request_enforces_capability_rules(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_READ_AUTHZ", "true")
    monkeypatch.setenv(
        "ENTERPRISE_CAPABILITY_RULES_JSON",
        json.dumps({"GET /reports/jobs/": "reports.jobs.read"}),
    )
    headers = {
        "X-Actor-Id": "a1",
        "X-Tenant-Id": "t1",
        "X-Role": "ops",
        "X-Correlation-Id": "c1",
        "X-Service-Identity": "portal",
        "X-Capabilities": "reports.jobs.write",
    }
    denied, denied_reason = authorize_read_request("GET", "/reports/jobs/r123", headers)
    assert denied is False
    assert denied_reason == "missing_capability:reports.jobs.read"

    headers["X-Capabilities"] = "reports.jobs.read,reports.jobs.write"
    allowed, allowed_reason = authorize_read_request("GET", "/reports/jobs/r123", headers)
    assert allowed is True
    assert allowed_reason is None


def test_authorize_read_request_matches_templated_capability_rules(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_READ_AUTHZ", "true")
    monkeypatch.setenv(
        "ENTERPRISE_CAPABILITY_RULES_JSON",
        json.dumps(
            {
                "POST /reports/jobs/{job_id}": "reports.jobs.write",
                "GET /reports/jobs/{job_id}/lineage": "reports.lineage.read",
            }
        ),
    )
    headers = {
        "X-Actor-Id": "a1",
        "X-Tenant-Id": "t1",
        "X-Role": "ops",
        "X-Correlation-Id": "c1",
        "X-Service-Identity": "portal",
        "X-Capabilities": "reports.lineage.read",
    }

    allowed, reason = authorize_read_request("GET", "/reports/jobs/r123/lineage", headers)

    assert allowed is True
    assert reason is None


def test_authorize_read_request_rejects_templated_rule_shape_mismatch(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_READ_AUTHZ", "true")
    monkeypatch.setenv(
        "ENTERPRISE_CAPABILITY_RULES_JSON",
        json.dumps({"GET /reports/jobs/{job_id}/lineage": "reports.lineage.read"}),
    )
    headers = {
        "X-Actor-Id": "a1",
        "X-Tenant-Id": "t1",
        "X-Role": "ops",
        "X-Correlation-Id": "c1",
        "X-Service-Identity": "portal",
    }

    allowed, reason = authorize_read_request("GET", "/reports/jobs/r123", headers)

    assert allowed is True
    assert reason is None


def test_authorize_write_request_supports_root_scoped_capability_rule(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "true")
    monkeypatch.setenv("ENTERPRISE_CAPABILITY_RULES_JSON", json.dumps({"POST /": "reports.write"}))
    headers = {
        "X-Actor-Id": "a1",
        "X-Tenant-Id": "t1",
        "X-Role": "ops",
        "X-Correlation-Id": "c1",
        "X-Service-Identity": "lotus-report",
        "X-Capabilities": "reports.write",
    }

    allowed, reason = authorize_write_request("POST", "/reports/export", headers)

    assert allowed is True
    assert reason is None


@pytest.mark.asyncio
async def test_middleware_audits_read_access_when_enabled(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_READ_AUTHZ", "false")
    monkeypatch.setenv("ENTERPRISE_AUDIT_READS", "true")
    middleware = build_enterprise_audit_middleware()
    audit_events: list[dict] = []

    def _record_audit(**kwargs):
        audit_events.append(kwargs)

    monkeypatch.setattr("app.enterprise_readiness.emit_audit_event", _record_audit)

    async def _call_next(_request):
        from fastapi.responses import JSONResponse

        return JSONResponse({"ok": True}, status_code=200)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/reports/jobs/r123",
        "headers": [
            (b"x-actor-id", b"a1"),
            (b"x-tenant-id", b"t1"),
            (b"x-role", b"ops"),
            (b"x-correlation-id", b"c1"),
        ],
    }
    request = _request(scope)
    response = await middleware(request, _call_next)
    assert response.status_code == 200
    assert len(audit_events) == 1
    assert audit_events[0]["action"] == "GET /reports/jobs/r123"
    assert audit_events[0]["metadata"]["access_type"] == "read"


@pytest.mark.asyncio
async def test_middleware_audits_write_access(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "false")
    middleware = build_enterprise_audit_middleware()
    audit_events: list[dict] = []

    def _record_audit(**kwargs):
        audit_events.append(kwargs)

    monkeypatch.setattr("app.enterprise_readiness.emit_audit_event", _record_audit)

    async def _call_next(_request):
        from fastapi.responses import JSONResponse

        return JSONResponse({"ok": True}, status_code=202)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/reports/export",
        "headers": [
            (b"x-actor-id", b"a1"),
            (b"x-tenant-id", b"t1"),
            (b"x-role", b"ops"),
            (b"x-correlation-id", b"c1"),
            (b"content-length", b"0"),
        ],
    }
    request = _request(scope)
    response = await middleware(request, _call_next)
    assert response.status_code == 202
    assert len(audit_events) == 1
    assert audit_events[0]["action"] == "POST /reports/export"
    assert audit_events[0]["metadata"]["status_code"] == 202


@pytest.mark.asyncio
async def test_middleware_read_denies_missing_service_identity(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_READ_AUTHZ", "true")
    monkeypatch.setenv("ENTERPRISE_AUDIT_READS", "false")
    middleware = build_enterprise_audit_middleware()
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/reports/jobs/r123",
        "headers": [
            (b"x-actor-id", b"a1"),
            (b"x-tenant-id", b"t1"),
            (b"x-role", b"ops"),
            (b"x-correlation-id", b"c1"),
        ],
    }
    request = _request(scope)
    response = await middleware(request, lambda req: None)  # pragma: no cover
    assert response.status_code == 403


def test_redaction_handles_list_payloads():
    redacted = redact_sensitive([{"token": "x"}, {"safe": "ok"}])
    assert redacted[0]["token"] == "***REDACTED***"
    assert redacted[1]["safe"] == "ok"
