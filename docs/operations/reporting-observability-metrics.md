# Reporting Observability Metrics

This page records the RFC-0105 Slice 3 first-wave `lotus-report` metrics contract, updated by
Slice 5 for the implementation-backed rerender-from-snapshot command, Slice 6 for the
implementation-backed regenerate-from-upstream command, Slice 7 for failed-work replay, and
Slice 8 for source-backed operations attention scans.

The current implementation emits metrics for report operations that already exist and are
implementation-backed. Dedicated broad replay dashboards remain reserved until those command paths
are implemented and proven.

## Implemented Metrics

| Metric | Type | Labels | Source |
| --- | --- | --- | --- |
| `lotus_report_operations_total` | counter | `operation`, `status`, `failure_category` | Report job submission, report-job worker pass, snapshot capture, render handoff, archive handoff, rerender-from-snapshot, regenerate-from-upstream, failed-work replay command, batch worker pass, scheduler pass |
| `lotus_report_operation_duration_seconds` | histogram | `operation`, `status`, `failure_category` | Duration for the same implemented operations |
| `lotus_report_job_runtime_last_items` | gauge | `item_state` | Latest bounded report-job worker pass counts for leased, succeeded, retry-pending, and failed work items |
| `lotus_report_batch_runtime_last_items` | gauge | `item_state` | Latest bounded batch-worker pass counts for recovered, leased, dispatched, and executed items |
| `lotus_report_batch_scheduler_last_schedules` | gauge | `outcome` | Latest bounded scheduler pass counts for attempted, materialized, and skipped schedules |
| `lotus_report_batch_pressure_last_counts` | gauge | `pressure_state` | Latest bounded durable batch pressure counts |
| `lotus_report_attention_events_last_count` | gauge | `attention_type`, `severity` | Latest source-backed operations attention scan counts for stuck-state and SLA-breach events |

## Reserved Metrics

| Metric | Status | Reason |
| --- | --- | --- |
| `lotus_report_replay_operations_total` | reserved | Dedicated broader replay dashboard metrics are not yet implementation-backed; failed-work replay is counted through `lotus_report_operations_total{operation="replay_command"}` |

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

The RFC-0108 evidence-surface supportability metric is intentionally lossy when callers provide
unexpected posture values. Unknown states are emitted as `state="unsupported"`, unknown reasons as
`reason="supportability_unsupported"`, and unknown freshness values as
`freshness_bucket="unknown"`. This keeps client, portfolio, tenant, trace, report, and raw upstream
details out of Prometheus while preserving an operator-visible degraded posture.

## Dashboard Contract

First-wave dashboards may reference only implemented metrics:

1. report operation volume and failure rate from `lotus_report_operations_total`,
2. render/archive latency from `lotus_report_operation_duration_seconds` filtered by operation,
3. report-job worker activity from `lotus_report_job_runtime_last_items`,
4. batch worker activity from `lotus_report_batch_runtime_last_items`,
5. scheduler materialization activity from `lotus_report_batch_scheduler_last_schedules`.

Dashboards may include `operation="rerender_from_snapshot"`,
`operation="regenerate_from_upstream"`, `operation="replay_command"`, and
`operation="stuck_state_scan"` from `lotus_report_operations_total` and
`lotus_report_operation_duration_seconds`. They may reference
`lotus_report_attention_events_last_count` for stuck-state and SLA-breach scan output. They must
not reference reserved dedicated broader replay metrics until those slices add implementation-backed
metrics and tests.

## Alert Contract

| Alert | Expression basis | Severity | Owner | Runbook action |
| --- | --- | --- | --- | --- |
| Report operation failure pressure | `lotus_report_operations_total{status="failed"}` over a 15 minute window | warning | reporting-operations | Inspect `/reports/jobs`, job events, snapshot lineage, render metadata, and archive handoff status |
| Render/archive latency pressure | `lotus_report_operation_duration_seconds` for `render_handoff` or `archive_handoff` over 5 minutes | warning | reporting-operations | Check `lotus-render` and `lotus-archive` health, logs, and downstream storage posture |
| Report-job worker inactivity | `lotus_report_job_runtime_last_items{item_state="succeeded"}` unchanged while runnable work items exist | warning | reporting-operations | Inspect report-job worker logs, expired leases, retry posture, and downstream source health |
| Batch worker inactivity | `lotus_report_batch_runtime_last_items{item_state="executed"}` unchanged while runnable batches exist | warning | reporting-operations | Inspect batch status, worker logs, and back-pressure limits |
| Scheduler materialization drop | `lotus_report_batch_scheduler_last_schedules{outcome="materialized"}` remains zero while enabled schedules are due | warning | reporting-operations | Inspect schedule configuration, scheduler logs, and portfolio selector source availability |
| Reporting attention pressure | `lotus_report_attention_events_last_count{severity="warning"}` over 15 minutes | warning | reporting-operations | Inspect `/reports/operations/attention`, report job diagnostics, batch item status, and replay only after retry eligibility is confirmed |
| Reporting SLA breach | `lotus_report_attention_events_last_count{attention_type="sla_breach",severity="critical"}` over 5 minutes | critical | reporting-operations | Escalate to reporting operations, inspect source-backed evidence links, recover expired leases, and confirm downstream render/archive health |

These alert contracts are initial thresholds for operator review. The attention scan endpoint emits
only opaque resource identifiers, bounded reasons, age, thresholds, and support-safe evidence links.
