"""Raw authority/replay cardinality must be settled before route admission."""

import json

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

from app.enterprise_readiness import build_enterprise_audit_middleware

SINGLETONS = (
    "authorization",
    "x-service-identity",
    "x-actor-id",
    "x-caller-application",
    "x-tenant-id",
    "x-region",
    "x-booking-center-code",
    "x-role",
    "x-correlation-id",
    "x-trace-id",
    "x-request-id",
    "traceparent",
    "idempotency-key",
)


@pytest.fixture
def authority(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_RUNTIME_PROFILE", "local")
    monkeypatch.setenv("ENTERPRISE_ENFORCE_AUTHZ", "true")
    monkeypatch.setenv(
        "ENTERPRISE_CAPABILITY_RULES_JSON",
        json.dumps({"POST /reports/portfolio-reviews": "reports.submit"}),
    )
    return [
        (name.encode(), value.encode())
        for name, value in {
            "x-actor-id": "synthetic-actor",
            "x-tenant-id": "synthetic-tenant",
            "x-role": "advisor",
            "x-correlation-id": "synthetic-correlation",
            "x-service-identity": "synthetic-service",
            "x-capabilities": "reports.submit",
            "x-caller-application": "lotus-gateway",
            "x-region": "APAC",
            "x-booking-center-code": "SG",
            "idempotency-key": "singleton-key",
        }.items()
    ]


async def _call(headers):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/reports/portfolio-reviews",
            "headers": headers,
        },
        receive,
    )
    admitted = []

    async def next_route(_request):
        admitted.append(True)
        return JSONResponse({"accepted": True}, status_code=202)

    response = await build_enterprise_audit_middleware()(request, next_route)
    return response, admitted


@pytest.mark.asyncio
@pytest.mark.parametrize("header", SINGLETONS)
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("identical", [False, True])
async def test_duplicate_scalar_headers_are_refused_before_route(
    authority, header, reverse, identical
):
    raw = [pair for pair in authority if pair[0].decode() != header]
    values = [b"synthetic-a", b"synthetic-a" if identical else b"synthetic-b"]
    if reverse:
        values.reverse()
    raw.extend([(header.upper().encode(), values[0]), (header.encode(), values[1])])
    response, admitted = await _call(raw)
    assert response.status_code == 400
    assert json.loads(response.body) == {
        "detail": {"code": "ambiguous_request_headers", "headers": [header]}
    }
    assert admitted == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_capability_header_lines_have_order_independent_list_semantics(authority, reverse):
    raw = [pair for pair in authority if pair[0] != b"x-capabilities"]
    values = [b"reports.read", b"reports.submit"]
    if reverse:
        values.reverse()
    raw.extend([(b"X-Capabilities", values[0]), (b"x-capabilities", values[1])])
    response, admitted = await _call(raw)
    assert response.status_code == 202 and admitted == [True]


@pytest.mark.asyncio
async def test_singleton_authority_remains_admitted(authority):
    response, admitted = await _call(authority)
    assert response.status_code == 202 and admitted == [True]


def test_declared_route_authority_headers_have_explicit_cardinality_policy():
    from app.main import app
    from app.request_header_admission import SINGLETON_REQUEST_HEADERS

    declared = {
        parameter["name"].lower()
        for path in app.openapi()["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict)
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "header"
    }
    assert declared - SINGLETON_REQUEST_HEADERS == {"x-capabilities"}


@pytest.mark.asyncio
async def test_ambiguity_audit_does_not_select_a_tenant_from_conflicting_headers(authority, caplog):
    import logging

    from app.observability import JsonFormatter

    caplog.set_level(logging.INFO, logger="enterprise_readiness")
    response, admitted = await _call(authority + [(b"X-Tenant-Id", b"foreign-tenant")])
    assert response.status_code == 400 and admitted == []
    records = [
        record for record in caplog.records if record.getMessage() == "enterprise_audit_event"
    ]
    assert len(records) == 1
    audit = json.loads(JsonFormatter().format(records[0]))["audit"]
    assert audit["tenant_id"] is None and audit["actor_id"] == "synthetic-actor"
    assert audit["metadata"] == {
        "status_code": 400,
        "reason": "ambiguous_request_headers",
        "headers": ["x-tenant-id"],
    }
