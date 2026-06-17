import json
import logging
import os
import re
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_fastapi_instrumentator import routing as prometheus_routing
from starlette.routing import Match, Mount

from app.reporting_metrics import validate_reporting_metric_contracts

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

CORRELATION_ID_HEADER = "X-Correlation-Id"
CORRELATION_ID_HEADER_ALIAS = "X-Correlation-ID"
REQUEST_ID_HEADER = "X-Request-Id"
TRACE_ID_HEADER = "X-Trace-Id"
TRACE_ID_HEADER_ALIAS = "X-Trace-ID"
TRACEPARENT_HEADER = "traceparent"

_W3C_TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_PROMETHEUS_ROUTING_PATCHED = False

OBSERVABILITY_LOG_FIELDS = frozenset(
    {
        "correlation_id",
        "request_id",
        "trace_id",
        "http_method",
        "endpoint",
        "latency_ms",
    }
)
SAFE_OPERATOR_LOOKUP_FIELDS = frozenset(
    {
        "report_job_id",
        "report_request_id",
        "report_batch_id",
        "report_batch_item_id",
        "snapshot_id",
        "render_job_id",
        "archive_request_id",
        "document_id",
        "correlation_id",
        "trace_id",
        "idempotency_key",
        "tenant_id",
        "region",
        "booking_center_code",
    }
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": os.getenv("SERVICE_NAME", "lotus-report"),
            "environment": os.getenv("ENVIRONMENT", "local"),
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id_var.get() or None,
            "request_id": request_id_var.get() or None,
            "trace_id": trace_id_var.get() or None,
        }
        if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
            payload.update(record.extra_fields)
        return json.dumps({k: v for k, v in payload.items() if v is not None})


def setup_logging() -> None:
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    root_logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root_logger.addHandler(handler)


def resolve_correlation_id(request: Request) -> str:
    incoming = request.headers.get(CORRELATION_ID_HEADER) or request.headers.get(
        CORRELATION_ID_HEADER_ALIAS
    )
    return incoming if incoming else f"corr_{uuid4().hex[:12]}"


def resolve_request_id(request: Request) -> str:
    incoming = request.headers.get(REQUEST_ID_HEADER)
    return incoming if incoming else f"req_{uuid4().hex[:12]}"


def resolve_trace_id(request: Request) -> str:
    traceparent = request.headers.get(TRACEPARENT_HEADER)
    if isinstance(traceparent, str) and traceparent:
        parts = traceparent.split("-")
        if len(parts) >= 4 and _is_w3c_trace_id(parts[1]):
            return parts[1]
    incoming = request.headers.get(TRACE_ID_HEADER) or request.headers.get(TRACE_ID_HEADER_ALIAS)
    if isinstance(incoming, str) and incoming:
        return incoming
    return uuid4().hex


def _is_w3c_trace_id(trace_id: str) -> bool:
    return bool(_W3C_TRACE_ID_PATTERN.fullmatch(trace_id))


def traceparent_header(trace_id: str) -> str | None:
    if not _is_w3c_trace_id(trace_id):
        return None
    return f"00-{trace_id}-0000000000000001-01"


def propagation_headers(correlation_id: str | None = None) -> dict[str, str]:
    resolved_trace = trace_id_var.get() or uuid4().hex
    resolved_correlation_id = (
        correlation_id or correlation_id_var.get() or f"corr_{uuid4().hex[:12]}"
    )
    headers = {
        CORRELATION_ID_HEADER: resolved_correlation_id,
        REQUEST_ID_HEADER: request_id_var.get() or f"req_{uuid4().hex[:12]}",
        TRACE_ID_HEADER: resolved_trace,
    }
    traceparent = traceparent_header(resolved_trace)
    if traceparent:
        headers[TRACEPARENT_HEADER] = traceparent
    return headers


def setup_observability(app: FastAPI) -> None:
    setup_logging()
    validate_reporting_metric_contracts()
    _install_fastapi_included_router_prometheus_patch()
    Instrumentator().instrument(app).expose(app)

    @app.middleware("http")
    async def _request_observability_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        logger = logging.getLogger("http.access")
        started = time.perf_counter()

        correlation_id = resolve_correlation_id(request)
        request_id = resolve_request_id(request)
        trace_id = resolve_trace_id(request)

        corr_token = correlation_id_var.set(correlation_id)
        req_token = request_id_var.set(request_id)
        trace_token = trace_id_var.set(trace_id)
        try:
            response = await call_next(request)
        finally:
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "request.completed",
                extra={
                    "extra_fields": {
                        "http_method": request.method,
                        "endpoint": request.url.path,
                        "latency_ms": latency_ms,
                    }
                },
            )
            correlation_id_var.reset(corr_token)
            request_id_var.reset(req_token)
            trace_id_var.reset(trace_token)

        response.headers[CORRELATION_ID_HEADER] = correlation_id
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[TRACE_ID_HEADER] = trace_id
        traceparent = traceparent_header(trace_id)
        if traceparent:
            response.headers[TRACEPARENT_HEADER] = traceparent
        return response


def _install_fastapi_included_router_prometheus_patch() -> None:
    global _PROMETHEUS_ROUTING_PATCHED
    if _PROMETHEUS_ROUTING_PATCHED:
        return
    prometheus_routing._get_route_name = _get_prometheus_route_name
    _PROMETHEUS_ROUTING_PATCHED = True


def _get_prometheus_route_name(
    scope: dict[str, Any],
    routes: list[Any],
    route_name: str | None = None,
) -> str | None:
    """Resolve route names across Starlette routes and FastAPI deferred routers."""

    for route in routes:
        match, child_scope = route.matches(scope)
        if match == Match.FULL:
            matched_route = _resolve_effective_route(route, scope)
            route_path = getattr(matched_route, "path", None)
            if not isinstance(route_path, str):
                return route_name

            child_scope = {**scope, **child_scope}
            route_name = route_path
            if isinstance(matched_route, Mount) and matched_route.routes:
                child_route_name = _get_prometheus_route_name(
                    child_scope, matched_route.routes, route_name
                )
                if child_route_name is None:
                    route_name = None
                else:
                    route_name += child_route_name
            return route_name
        if match == Match.PARTIAL and route_name is None:
            route_path = getattr(route, "path", None)
            if isinstance(route_path, str):
                route_name = route_path
    return None


def _resolve_effective_route(route: Any, scope: dict[str, Any]) -> Any:
    match_method = getattr(route, "_match", None)
    if not callable(match_method):
        return route

    try:
        _match, _child_scope, matched_route, effective_context = match_method(scope)
    except Exception:
        return route
    if matched_route is not None:
        return matched_route
    starlette_route = getattr(effective_context, "starlette_route", None)
    return starlette_route or route
