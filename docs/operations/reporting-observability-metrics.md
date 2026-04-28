# Reporting Observability Metrics

This page records the RFC-0105 Slice 3 first-wave `lotus-report` metrics contract, updated by
Slice 5 for the implementation-backed rerender-from-snapshot command.

The current implementation emits metrics for report operations that already exist and are
implementation-backed. Broader replay, regenerate, and stuck-state scan metrics are reserved until
those command paths are implemented and proven.

## Implemented Metrics

| Metric | Type | Labels | Source |
| --- | --- | --- | --- |
| `lotus_report_operations_total` | counter | `operation`, `status`, `failure_category` | Report job submission, snapshot capture, render handoff, archive handoff, rerender-from-snapshot, batch worker pass, scheduler pass |
| `lotus_report_operation_duration_seconds` | histogram | `operation`, `status`, `failure_category` | Duration for the same implemented operations |
| `lotus_report_batch_runtime_last_items` | gauge | `item_state` | Latest bounded batch-worker pass counts for recovered, leased, dispatched, and executed items |
| `lotus_report_batch_scheduler_last_schedules` | gauge | `outcome` | Latest bounded scheduler pass counts for attempted, materialized, and skipped schedules |

## Reserved Metrics

| Metric | Status | Reason |
| --- | --- | --- |
| `lotus_report_replay_operations_total` | reserved | Broader replay and regenerate commands are not yet implementation-backed |

## Label Discipline

Metrics must use bounded operational labels only. These values are intentionally excluded from
metric labels:

1. account, client, portfolio, tenant, booking-center, and document identifiers,
2. report job, batch, item, render, archive, snapshot, idempotency, correlation, request, and trace
   identifiers,
3. object-storage bucket or storage-key values,
4. raw upstream payloads and client or portfolio names.

The code-owned metric contract in `src/app/reporting_metrics.py` rejects unsupported and forbidden
labels before the FastAPI application exposes `/metrics`.

## Dashboard Contract

First-wave dashboards may reference only implemented metrics:

1. report operation volume and failure rate from `lotus_report_operations_total`,
2. render/archive latency from `lotus_report_operation_duration_seconds` filtered by operation,
3. batch worker activity from `lotus_report_batch_runtime_last_items`,
4. scheduler materialization activity from `lotus_report_batch_scheduler_last_schedules`.

Dashboards may include `operation="rerender_from_snapshot"` from
`lotus_report_operations_total` and `lotus_report_operation_duration_seconds`. They must not
reference reserved broader replay, regenerate, stuck-state, or SLA scan metrics until those slices
add implementation-backed metrics and tests.

## Alert Contract

| Alert | Expression basis | Severity | Owner | Runbook action |
| --- | --- | --- | --- | --- |
| Report operation failure pressure | `lotus_report_operations_total{status="failed"}` over a 15 minute window | warning | reporting-operations | Inspect `/reports/jobs`, job events, snapshot lineage, render metadata, and archive handoff status |
| Render/archive latency pressure | `lotus_report_operation_duration_seconds` for `render_handoff` or `archive_handoff` over 5 minutes | warning | reporting-operations | Check `lotus-render` and `lotus-archive` health, logs, and downstream storage posture |
| Batch worker inactivity | `lotus_report_batch_runtime_last_items{item_state="executed"}` unchanged while runnable batches exist | warning | reporting-operations | Inspect batch status, worker logs, and back-pressure limits |
| Scheduler materialization drop | `lotus_report_batch_scheduler_last_schedules{outcome="materialized"}` remains zero while enabled schedules are due | warning | reporting-operations | Inspect schedule configuration, scheduler logs, and portfolio selector source availability |

These alert contracts are initial thresholds for operator review. RFC-0105 later slices must tighten
SLA breach and stuck-state alerts after the corresponding status and diagnostics APIs are complete.
