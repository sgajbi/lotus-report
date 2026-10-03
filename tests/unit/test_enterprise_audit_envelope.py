"""The actual enterprise emitter and shipped formatter share the audit envelope."""

import io
import json
import logging
from datetime import datetime

import pytest
from fastapi import Request

from app.enterprise_readiness import build_enterprise_audit_middleware, emit_audit_event
from app.observability import JsonFormatter, correlation_id_var, request_id_var, trace_id_var


@pytest.fixture
def serialized_audit(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_POLICY_VERSION", "audit-synthetic-v1")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("enterprise_readiness")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        yield stream
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(previous_level)


def _emit(action, metadata):
    emit_audit_event(
        action=action,
        actor_id="synthetic-actor",
        tenant_id="synthetic-tenant",
        role="advisor",
        correlation_id="synthetic-audit-correlation",
        metadata=metadata,
    )


@pytest.mark.parametrize(
    "action,metadata",
    [
        ("POST /reports/portfolio-reviews", {"status_code": 202}),
        ("POST /reports/portfolio-reviews", {"status_code": 409}),
        ("POST /reports/portfolio-reviews", {"status_code": 503}),
        ("DENY POST /reports/portfolio-reviews", {"reason": "missing_required_headers"}),
        ("GET /reports/jobs/synthetic", {"status_code": 200, "access_type": "read"}),
    ],
)
def test_actual_audit_emitter_and_formatter_preserve_identity_and_outcome(
    serialized_audit, action, metadata
):
    _emit(action, metadata)
    lines = serialized_audit.getvalue().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["logger"] == "enterprise_readiness"
    assert payload["message"] == "enterprise_audit_event"
    audit = payload["audit"]
    assert audit["service"] == "lotus-report" and audit["action"] == action
    assert audit["actor_id"] == "synthetic-actor" and audit["tenant_id"] == "synthetic-tenant"
    assert audit["role"] == "advisor" and audit["correlation_id"] == "synthetic-audit-correlation"
    assert audit["policy_version"] == "audit-synthetic-v1" and audit["metadata"] == metadata
    assert datetime.fromisoformat(audit["timestamp_utc"]).utcoffset().total_seconds() == 0


def test_actual_audit_serialization_retains_recursive_redaction(serialized_audit):
    metadata = {
        "status_code": 202,
        "Password": "password-secret",
        "nested": {
            "token": "token-secret",
            "safe": "operator-safe",
            "children": (
                {"authorization": "authorization-secret"},
                {"account_number": "account-secret"},
            ),
        },
    }
    _emit("POST /reports/portfolio-reviews", metadata)
    text = serialized_audit.getvalue()
    audit = json.loads(text)["audit"]
    for secret in ("password-secret", "token-secret", "authorization-secret", "account-secret"):
        assert secret not in text
    assert audit["metadata"] == {
        "status_code": 202,
        "Password": "***REDACTED***",
        "nested": {
            "token": "***REDACTED***",
            "safe": "operator-safe",
            "children": [
                {"authorization": "***REDACTED***"},
                {"account_number": "***REDACTED***"},
            ],
        },
    }
    assert metadata["nested"]["token"] == "token-secret"


@pytest.mark.parametrize(
    "field",
    [
        "service",
        "environment",
        "logger",
        "level",
        "message",
        "timestamp",
        "correlation_id",
        "request_id",
        "trace_id",
        "audit",
    ],
)
def test_generic_extras_cannot_replace_protected_log_or_audit_fields(field):
    tokens = [
        (correlation_id_var, correlation_id_var.set("actual-correlation")),
        (request_id_var, request_id_var.set("actual-request")),
        (trace_id_var, trace_id_var.set("actual-trace")),
    ]
    try:
        record = logging.LogRecord(
            "actual.logger", logging.INFO, __file__, 1, "actual-message", (), None
        )
        record.extra_fields = {field: "forged", "endpoint": "/safe"}
        payload = json.loads(JsonFormatter().format(record))
        assert payload.get(field) != "forged"
        assert payload["endpoint"] == "/safe"
        assert "audit" not in payload
        assert payload["correlation_id"] == "actual-correlation"
        assert payload["request_id"] == "actual-request" and payload["trace_id"] == "actual-trace"
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


@pytest.mark.parametrize(
    "malformed", [None, [], "untrusted-detail", {"action": "untrusted-detail"}]
)
def test_malformed_audit_envelope_is_refused_without_raw_output(malformed):
    record = logging.LogRecord(
        "enterprise_readiness", logging.INFO, __file__, 1, "enterprise_audit_event", (), None
    )
    record.audit = malformed
    payload = json.loads(JsonFormatter().format(record))
    assert payload["audit_serialization_error"] == "invalid_audit_envelope"
    assert "audit" not in payload and "untrusted-detail" not in json.dumps(payload)


@pytest.mark.parametrize("value", [object(), float("nan"), float("inf"), float("-inf")])
def test_actual_emitter_refuses_non_json_metadata_safely(serialized_audit, value):
    _emit("POST /reports/portfolio-reviews", {"unsupported": value})
    payload = json.loads(serialized_audit.getvalue())
    assert payload["audit_serialization_error"] == "invalid_audit_envelope"
    assert "audit" not in payload and "unsupported" not in json.dumps(payload)


@pytest.mark.parametrize("size,valid", [(8180, True), (8192, False)])
def test_audit_metadata_byte_budget_accepts_valid_and_refuses_oversized(
    serialized_audit, size, valid
):
    _emit("POST /reports/portfolio-reviews", {"safe": "a" * size})
    payload = json.loads(serialized_audit.getvalue())
    if valid:
        assert payload["audit"]["metadata"] == {"safe": "a" * size}
    else:
        assert payload["audit_serialization_error"] == "invalid_audit_envelope"
        assert "audit" not in payload


@pytest.mark.asyncio
async def test_actual_denial_serialization_does_not_invent_absent_identity(
    serialized_audit, monkeypatch
):
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "true")
    request = Request(
        {"type": "http", "method": "POST", "path": "/reports/portfolio-reviews", "headers": []}
    )
    response = await build_enterprise_audit_middleware()(request, lambda _request: None)
    assert response.status_code == 403
    audit = json.loads(serialized_audit.getvalue())["audit"]
    assert audit["actor_id"] is None and audit["tenant_id"] is None and audit["role"] is None
    assert audit["correlation_id"] is None
    assert audit["metadata"] == {
        "status_code": 403,
        "reason": "missing_headers:x-actor-id,x-correlation-id,x-role,x-tenant-id",
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_id", 123),
        ("tenant_id", "a" * 257),
        ("action", "a" * 1025),
        ("policy_version", "a" * 65),
        ("timestamp_utc", "2026-10-03T20:00:00"),
        ("schema_version", "unknown-audit-schema"),
        ("unrecognized_field", "untrusted-detail"),
    ],
)
def test_formatter_refuses_malformed_typed_fields_without_detail(serialized_audit, field, value):
    _emit("POST /reports/portfolio-reviews", {"status_code": 202})
    envelope = json.loads(serialized_audit.getvalue())["audit"]
    envelope[field] = value
    record = logging.LogRecord(
        "enterprise_readiness", logging.INFO, __file__, 1, "enterprise_audit_event", (), None
    )
    record.audit = envelope
    payload = json.loads(JsonFormatter().format(record))
    assert payload["audit_serialization_error"] == "invalid_audit_envelope"
    assert "audit" not in payload and "untrusted-detail" not in json.dumps(payload)


def test_actual_emitter_accepts_identifier_budget_boundary(serialized_audit):
    emit_audit_event(
        action="POST /reports/portfolio-reviews",
        actor_id="a" * 256,
        tenant_id="synthetic-tenant",
        role="advisor",
        correlation_id="synthetic-correlation",
        metadata={"status_code": 202},
    )
    assert json.loads(serialized_audit.getvalue())["audit"]["actor_id"] == "a" * 256
