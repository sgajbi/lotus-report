import json
import logging

import pytest
from fastapi import Request

import app.reporting_metrics as reporting_metrics
from app.observability import (
    CORRELATION_ID_HEADER,
    OBSERVABILITY_LOG_FIELDS,
    REQUEST_ID_HEADER,
    SAFE_OPERATOR_LOOKUP_FIELDS,
    TRACE_ID_HEADER,
    TRACEPARENT_HEADER,
    JsonFormatter,
    correlation_id_var,
    propagation_headers,
    request_id_var,
    resolve_correlation_id,
    resolve_request_id,
    resolve_trace_id,
    setup_logging,
    trace_id_var,
)
from app.report_batch_orchestrator.models import BatchPressureSnapshot
from app.reporting_metrics import (
    FORBIDDEN_METRIC_LABELS,
    IMPLEMENTED_REPORTING_OPERATIONS,
    REPORTING_METRIC_CONTRACTS,
    RESERVED_REPORTING_OPERATIONS,
    ReportingMetricContract,
    record_batch_pressure_metrics,
    record_batch_scheduler_metrics,
    record_batch_worker_metrics,
    record_report_operation,
    validate_reporting_metric_contracts,
)


def _request_with_headers(headers: dict[str, str]) -> Request:
    asgi_headers = [(k.lower().encode("utf-8"), v.encode("utf-8")) for k, v in headers.items()]
    scope = {"type": "http", "headers": asgi_headers}
    return Request(scope)


def test_resolve_correlation_id_prefers_primary_header():
    request = _request_with_headers({"X-Correlation-Id": "corr-primary"})
    assert resolve_correlation_id(request) == "corr-primary"


def test_resolve_correlation_id_accepts_alias_header():
    request = _request_with_headers({"X-Correlation-ID": "corr-alias"})
    assert resolve_correlation_id(request) == "corr-alias"


def test_resolve_request_id_generates_when_missing():
    request = _request_with_headers({})
    value = resolve_request_id(request)
    assert value.startswith("req_")
    assert len(value) > 8


def test_resolve_trace_id_prefers_traceparent():
    request = _request_with_headers(
        {"traceparent": "00-0123456789abcdef0123456789abcdef-0000000000000001-01"}
    )
    assert resolve_trace_id(request) == "0123456789abcdef0123456789abcdef"


def test_resolve_trace_id_falls_back_to_x_trace_id():
    request = _request_with_headers({"X-Trace-Id": "trace-x"})
    assert resolve_trace_id(request) == "trace-x"


def test_resolve_trace_id_accepts_uppercase_id_alias():
    request = _request_with_headers({"X-Trace-ID": "trace-alias"})
    assert resolve_trace_id(request) == "trace-alias"


def test_resolve_trace_id_uses_x_trace_id_when_traceparent_malformed():
    request = _request_with_headers(
        {"traceparent": "00-short-0000000000000001-01", "X-Trace-Id": "trace-x"}
    )
    assert resolve_trace_id(request) == "trace-x"


def test_propagation_headers_include_context_values():
    correlation_id_var.set("corr-ctx")
    request_id_var.set("req-ctx")
    trace_id_var.set("0123456789abcdef0123456789abcdef")
    headers = propagation_headers()
    assert headers[CORRELATION_ID_HEADER] == "corr-ctx"
    assert headers[REQUEST_ID_HEADER] == "req-ctx"
    assert headers[TRACE_ID_HEADER] == "0123456789abcdef0123456789abcdef"
    assert headers[TRACEPARENT_HEADER] == "00-0123456789abcdef0123456789abcdef-0000000000000001-01"


def test_propagation_headers_omit_invalid_w3c_traceparent():
    correlation_id_var.set("corr-ctx")
    request_id_var.set("req-ctx")
    trace_id_var.set("trace-human-readable")

    headers = propagation_headers()

    assert headers[TRACE_ID_HEADER] == "trace-human-readable"
    assert TRACEPARENT_HEADER not in headers


def test_observability_contract_declares_safe_runtime_and_operator_fields():
    assert {"correlation_id", "request_id", "trace_id", "latency_ms"}.issubset(
        OBSERVABILITY_LOG_FIELDS
    )
    safe_lookup_fields = {
        "report_job_id",
        "report_batch_id",
        "document_id",
        "correlation_id",
        "trace_id",
    }
    assert safe_lookup_fields.issubset(SAFE_OPERATOR_LOOKUP_FIELDS)
    assert "portfolio_name" not in SAFE_OPERATOR_LOOKUP_FIELDS
    assert "client_name" not in SAFE_OPERATOR_LOOKUP_FIELDS
    assert "raw_upstream_payload" not in SAFE_OPERATOR_LOOKUP_FIELDS


def test_json_formatter_emits_structured_payload_with_extra_fields(monkeypatch):
    monkeypatch.setenv("SERVICE_NAME", "ras-test")
    monkeypatch.setenv("ENVIRONMENT", "test")
    correlation_id_var.set("corr-log")
    request_id_var.set("req-log")
    trace_id_var.set("trace-log")
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="unit.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="log-message",
        args=(),
        exc_info=None,
    )
    record.extra_fields = {"endpoint": "/health", "latency_ms": 12.5}
    payload = json.loads(formatter.format(record))
    assert payload["service"] == "ras-test"
    assert payload["environment"] == "test"
    assert payload["correlation_id"] == "corr-log"
    assert payload["message"] == "log-message"
    assert payload["endpoint"] == "/health"
    assert payload["latency_ms"] == 12.5


def test_setup_logging_initializes_handler_when_root_has_no_handlers():
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    setup_logging()
    assert root_logger.hasHandlers()


def test_reporting_metric_contracts_are_bounded_and_implementation_truthful():
    validate_reporting_metric_contracts()
    implemented_names = {
        contract.name for contract in REPORTING_METRIC_CONTRACTS if contract.implemented
    }
    reserved_names = {
        contract.name for contract in REPORTING_METRIC_CONTRACTS if not contract.implemented
    }

    assert "lotus_report_operations_total" in implemented_names
    assert "lotus_report_batch_pressure_last_counts" in implemented_names
    assert "lotus_report_replay_operations_total" in reserved_names
    assert {
        "report_job_submission",
        "snapshot_capture",
        "render_handoff",
        "archive_handoff",
        "rerender_from_snapshot",
        "regenerate_from_upstream",
    } <= (IMPLEMENTED_REPORTING_OPERATIONS)
    assert {"replay_command", "rerender_command", "regenerate_command"} <= (
        RESERVED_REPORTING_OPERATIONS
    )
    for contract in REPORTING_METRIC_CONTRACTS:
        assert not (set(contract.labels) & FORBIDDEN_METRIC_LABELS)
        assert "correlation_id" not in contract.labels
        assert "trace_id" not in contract.labels
        assert "portfolio_id" not in contract.labels
        assert "client_name" not in contract.labels


def test_record_report_operation_rejects_unimplemented_reserved_operation():
    with pytest.raises(ValueError, match="unsupported_reporting_metric_operation"):
        record_report_operation(operation="replay_command", status="failed")


def test_reporting_metric_contract_validation_rejects_duplicate_metric_names(monkeypatch):
    duplicate_contracts = REPORTING_METRIC_CONTRACTS + (REPORTING_METRIC_CONTRACTS[0],)
    monkeypatch.setattr(reporting_metrics, "REPORTING_METRIC_CONTRACTS", duplicate_contracts)

    with pytest.raises(ValueError, match="duplicate_reporting_metric_name"):
        validate_reporting_metric_contracts()


def test_reporting_metric_contract_validation_rejects_reserved_implemented_metric(monkeypatch):
    reserved_implemented = ReportingMetricContract(
        name="lotus_report_replay_operations_total",
        metric_type="counter",
        labels=("operation", "status", "failure_category"),
        implemented=True,
        description="invalid reserved implementation marker",
    )
    monkeypatch.setattr(reporting_metrics, "REPORTING_METRIC_CONTRACTS", (reserved_implemented,))

    with pytest.raises(ValueError, match="reserved_replay_metric_marked_implemented"):
        validate_reporting_metric_contracts()


def test_reporting_metric_contract_validation_rejects_forbidden_and_unsupported_labels(
    monkeypatch,
):
    forbidden_label_contract = ReportingMetricContract(
        name="lotus_report_invalid_forbidden_label_total",
        metric_type="counter",
        labels=("operation", "portfolio_id"),
        implemented=True,
        description="invalid high-cardinality label",
    )
    monkeypatch.setattr(
        reporting_metrics,
        "REPORTING_METRIC_CONTRACTS",
        (forbidden_label_contract,),
    )

    with pytest.raises(ValueError, match="forbidden_metric_label:portfolio_id"):
        validate_reporting_metric_contracts()

    unsupported_label_contract = ReportingMetricContract(
        name="lotus_report_invalid_unsupported_label_total",
        metric_type="counter",
        labels=("operation", "workflow_stage"),
        implemented=True,
        description="invalid non-contract label",
    )
    monkeypatch.setattr(
        reporting_metrics,
        "REPORTING_METRIC_CONTRACTS",
        (unsupported_label_contract,),
    )

    with pytest.raises(ValueError, match="unsupported_metric_label:workflow_stage"):
        validate_reporting_metric_contracts()


def test_record_report_operation_bounds_status_failure_category_and_duration():
    record_report_operation(
        operation="report_job_submission",
        status="accepted",
        failure_category=None,
        duration_seconds=0.01,
    )
    record_report_operation(
        operation="snapshot_capture",
        status="not-a-contract-status",
        failure_category=" Upstream-Timeout ",
        duration_seconds=-1.0,
    )
    record_report_operation(
        operation="render_handoff",
        status="failed",
        failure_category="",
    )
    record_report_operation(
        operation="render_handoff",
        status="failed",
        failure_category="   ",
    )
    record_report_operation(
        operation="archive_handoff",
        status="failed",
        failure_category="storage failure!",
    )
    record_report_operation(
        operation="archive_handoff",
        status="failed",
        failure_category="x" * 81,
    )
    record_report_operation(
        operation="rerender_from_snapshot",
        status="archived",
        failure_category=None,
        duration_seconds=0.01,
    )


def test_record_batch_worker_metrics_clamps_counts_and_classifies_skips():
    record_batch_worker_metrics(
        recovered_count=-1,
        leased_count=-2,
        dispatched_count=-3,
        executed_count=-4,
        duration_seconds=0.01,
    )
    record_batch_worker_metrics(
        recovered_count=1,
        leased_count=2,
        dispatched_count=3,
        executed_count=4,
        skipped_reason="batch_not_runnable:paused",
        duration_seconds=0.02,
    )
    record_batch_worker_metrics(
        recovered_count=0,
        leased_count=0,
        dispatched_count=0,
        executed_count=0,
        skipped_reason="max_active_items_reached",
    )


def test_record_batch_scheduler_metrics_clamps_counts():
    record_batch_scheduler_metrics(
        attempted_count=-1,
        materialized_count=-2,
        skipped_count=-3,
        duration_seconds=0.01,
    )


def test_record_batch_pressure_metrics_clamps_counts() -> None:
    record_batch_pressure_metrics(
        BatchPressureSnapshot(
            runnable_batches=-1,
            active_batches=2,
            active_items=-3,
            dispatch_ready_items=4,
            retry_ready_items=-5,
            recovery_pending_items=6,
        )
    )
