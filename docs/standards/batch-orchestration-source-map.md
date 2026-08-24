# Batch Orchestration Source Map

RFC-0104 batch materialization is source-backed. Slice 2 persists batch and batch-item truth.
Slice 3 adds deterministic schedule-cycle materialization and scheduled-batch identity primitives.
Slice 4 adds internal dispatch, lease, and back-pressure primitives. Slice 5 adds internal
bounded retry, pause/resume, cancellation-boundary, and expired-lease recovery primitives. Slice 6
adds certified materialization, status, and control APIs. Slice 7 adds an internal item execution
bridge over existing report-job, snapshot, render, and archive handoff paths. Slice 10 adds an
internal bounded single-batch worker run primitive. Slice 11 exposes that bounded worker pass
through an internal operator `run-once` API. Slice 12 adds an internal bounded runtime pass that
scans durable runnable batches and invokes the single-batch worker for a limited number of batches.
Slice 13 adds the daemonized internal `lotus-report-batch-worker` process over that runtime pass.
Slice 14 adds the daemonized internal `lotus-report-batch-scheduler` process that reads governed
schedule configuration and materializes durable idempotent scheduled batches for the worker to
execute. Slice 15 extends scheduler materialization to explicit portfolio-list, all-active, and
inline manifest selector modes. Slice 16 adds config-backed scheduler administration APIs for
listing schedules and running one bounded materialization pass. Schedule CRUD, Workbench
scheduler-management surfaces, and entitlement-certified public scheduler runtime remain later
slices.

| Attribute | Business meaning | Source application | Source object / contract | Current status |
| --- | --- | --- | --- | --- |
| `portfolio_id` | Portfolio included in the batch | `lotus-core` | `PortfolioScope` candidate identity | Available and used |
| `tenant_id` | Tenant ownership boundary for the portfolio | `lotus-core` | `PortfolioScope.tenant_id` | Available and used |
| `region` | Regional ownership boundary for the portfolio | `lotus-core` | `PortfolioScope.region` | Available and used |
| `active` | Whether the portfolio is reportable | `lotus-core` | Portfolio lifecycle status projected as active/inactive | Available and used |
| `selected` | Caller-selected subset membership | `lotus-report` derived composition from caller selection over `lotus-core` candidates | `PortfolioBatchCandidate.selected` | Available and used for `selected_subset` |
| `selector_mode` | Materialization strategy for the batch | `lotus-report` | RFC-0104 batch selector vocabulary | Available and used |
| `as_of_date` | Business date for every materialized report item | `lotus-report` caller request | `BatchCreateRequest.as_of_date` | Available and used |
| `requested_output_formats` | Output formats for future per-item report jobs | `lotus-report` caller request | `BatchCreateRequest.requested_output_formats` | Available and used |
| `reporting_currency` | Reporting currency for future per-item report jobs | `lotus-report` caller request | `BatchCreateRequest.reporting_currency` | Available and used |
| `options` | Output-affecting report options | `lotus-report` caller request | `BatchCreateRequest.options` | Available and used |
| `idempotency_key` | Caller identity for duplicate-safe batch creation | `lotus-gateway` / caller | `Idempotency-Key` equivalent for future batch API | Available and used |
| `request_hash` | Canonical compatibility hash for idempotency conflict detection | `lotus-report` derived composition | `compute_batch_request_hash` | Available and used |
| `frequency` | Production cadence for a scheduled batch cycle | `lotus-report` | RFC-0104 batch frequency vocabulary | Available and used internally |
| `period_start` | First business date included in a scheduled cycle | `lotus-report` derived composition | `BatchCycle.period_start` | Available and used internally |
| `period_end` | Last business date included in a scheduled cycle | `lotus-report` derived composition | `BatchCycle.period_end` | Available and used internally |
| `template_id` | Report template identity included in scheduled-cycle identity | `lotus-render` / `lotus-report` configuration | `BatchCycleRequest.template_id` | Available and used internally |
| `template_version` | Report template version included in scheduled-cycle identity | `lotus-render` / `lotus-report` configuration | `BatchCycleRequest.template_version` | Available and used internally |
| `render_package_version` | Render package contract version included in scheduled-cycle identity | `lotus-render` / `lotus-report` configuration | `BatchCycleRequest.render_package_version` | Available and used internally |
| `idempotency_scope` | Stable scheduled-cycle identity across retry attempts | `lotus-report` derived composition | `BatchCycle.idempotency_scope` | Available and used internally |
| `report_job_id` | Durable report job created or reused for one batch item | `lotus-report` | RFC-0100 `report_job.report_job_id` via `ReportJobLedger.create_portfolio_review_job` | Available and used internally |
| `report_job_status` | Current lifecycle state of the report job linked to one batch item | `lotus-report` | RFC-0100/RFC-0102/RFC-0103 `report_job.status`, composed by `status_projection.py` | Available in batch and item status; null before linking or when a legacy/inconsistent job link cannot be resolved |
| `snapshot_id` | Durable immutable report input snapshot linked to the report job | `lotus-report` | RFC-0101 `report_input_snapshot.snapshot_id` via snapshot capture and archive metadata | Available and used internally |
| `render_job_id` | Render execution identity for PDF jobs | `lotus-render` / `lotus-report` | RFC-0102 render response persisted on the report job and archive metadata | Available and used internally |
| `archive_document_id` | Archived rendered document identity for the exact report job linked to one batch item | `lotus-archive` / `lotus-report` | RFC-0103 archive response persisted on `report_job.archive_document_id`, composed by `status_projection.py` | Available in batch and item status only when the linked job is `archived`; null while archive is delayed or unavailable. Corrections and replacements are resolved through Archive metadata, never inferred by batch status |
| `lease_owner` | Worker identity that currently owns an in-flight batch item lease | `lotus-report` runtime worker identity | `ReportBatchDispatcher.dispatch_batch(worker_id=...)` | Available and used internally |
| `lease_token` | Opaque token required to heartbeat or dispatch a leased item | `lotus-report` derived composition | `ReportBatchLedger.acquire_dispatch_items` | Available and used internally |
| `lease_acquired_at` | Timestamp when the item lease was acquired | `lotus-report` runtime clock | Batch ledger dispatch fields | Available and used internally |
| `lease_expires_at` | Timestamp after which another worker may acquire the item | `lotus-report` runtime clock and `BatchDispatchPolicy.lease_seconds` | Batch ledger dispatch fields | Available and used internally |
| `last_heartbeat_at` | Last lease heartbeat timestamp for an in-flight item | `lotus-report` runtime clock | `heartbeat_item_lease` | Available and used internally |
| `dispatched_at` | Timestamp when a leased item was linked to a report job | `lotus-report` runtime clock | `mark_item_waiting_on_report_job` | Available and used internally |
| `max_active_batches` | Back-pressure limit for concurrent active batches | `lotus-report` configuration | `BatchDispatchPolicy.max_active_batches` | Available and used internally |
| `max_active_items` | Back-pressure limit for in-flight batch items | `lotus-report` configuration | `BatchDispatchPolicy.max_active_items` | Available and used internally |
| `max_active_upstream_jobs` | Back-pressure limit for upstream data-collection work | `lotus-report` configuration / future runtime telemetry | `BatchDispatchPolicy.max_active_upstream_jobs` and `BatchRuntimeLoad.active_upstream_jobs` | Available and used internally |
| `max_active_render_jobs` | Back-pressure limit for render work | `lotus-report` configuration / future render runtime telemetry | `BatchDispatchPolicy.max_active_render_jobs` and `BatchRuntimeLoad.active_render_jobs` | Available and used internally |
| `max_active_archive_jobs` | Back-pressure limit for archive work | `lotus-report` configuration / future archive runtime telemetry | `BatchDispatchPolicy.max_active_archive_jobs` and `BatchRuntimeLoad.active_archive_jobs` | Available and used internally |
| `attempt_count` | Number of dispatch/failure attempts recorded for an item | `lotus-report` derived runtime state | `ReportBatchItemRecord.attempt_count` | Available and used internally |
| `retry_eligible` | Whether a failed item may be retried by retry-failed-only control logic | `lotus-report` derived runtime state | `mark_item_failed`, `retry_failed_items` | Available and used internally |
| `next_retry_at` | Earliest retry time for a retryable failed item | `lotus-report` runtime clock and retry policy | `mark_item_failed` | Available and used internally |
| `last_error_category` | Safe failure category for retry/recovery decisions | `lotus-report` derived runtime state | `mark_item_failed`, `recover_expired_leases` | Available and used internally |
| `last_error_summary` | Client-safe or operator-safe failure summary without raw stack traces | `lotus-report` derived runtime state | `mark_item_failed`, `recover_expired_leases` | Available and used internally |
| `started_at` | Timestamp when a batch or item first started execution | `lotus-report` runtime clock | Batch and item ledger runtime fields | Available and used internally |
| `completed_at` | Timestamp when an item or aggregate batch reached terminal success/failure posture | `lotus-report` runtime clock | Batch and item ledger runtime fields | Available and used internally |
| `cancelled_at` | Timestamp when a batch or undispatched item was cancelled | `lotus-report` runtime clock | `cancel_batch` | Available and used internally |
| `failed_at` | Timestamp when aggregate batch posture became failed | `lotus-report` runtime clock | Batch ledger status reconciliation | Available and used internally |
| `max_attempts` | Retry ceiling that prevents unbounded item reruns | `lotus-report` configuration | `BatchRetryPolicy.max_attempts` | Available and used internally |
| `worker_id` | Operator-chosen worker identity for a bounded internal batch run | `lotus-report` runtime worker identity | `ReportBatchWorker.run_once(worker_id=...)` | Available and used internally |
| `worker_recovered_count` | Number of expired pre-dispatch leases recovered before dispatch | `lotus-report` derived runtime state | `BatchWorkerRunResult.recovered_count` | Available and used internally |
| `worker_dispatched_count` | Number of batch items linked to report jobs in one bounded run | `lotus-report` derived runtime state | `BatchWorkerRunResult.dispatched_count` | Available and used internally |
| `worker_executed_count` | Number of waiting batch items advanced through execution in one bounded run | `lotus-report` derived runtime state | `BatchWorkerRunResult.executed_count` | Available and used internally |
| `worker_back_pressure_reasons` | Safe reasons that new dispatch was skipped during one bounded run | `lotus-report` derived runtime state | `BatchWorkerRunResult.back_pressure_reasons` | Available and used internally |
| `run_once_worker_id` | Operator-supplied worker identity for one bounded API-triggered pass | `lotus-report` caller request | `BatchWorkerRunRequest.worker_id` and `POST /reports/batches/{batch_id}:run-once` | Available and used |
| `run_once_dispatch_policy` | Optional explicit dispatch limits for one bounded API-triggered pass | `lotus-report` caller request | `BatchWorkerRunRequest.dispatch_policy` | Available and used |
| `run_once_execution_results` | Product-safe item execution outcomes returned to the operator | `lotus-report` derived runtime state | `BatchWorkerRunResponse.execution_results` | Available and used |
| `runtime_runnable_batch_ids` | Durable ordered batch candidates that can advance in a bounded runtime pass | `lotus-report` PostgreSQL batch ledger | `list_runnable_batch_ids` over `report_batch` and `report_batch_item` state | Available and used internally |
| `runtime_max_batches` | Maximum number of candidate batches advanced by one bounded runtime pass | `lotus-report` runtime caller | `ReportBatchRuntime.run_pass(max_batches=...)` | Available and used internally |
| `runtime_back_pressure_stop` | Whether a runtime pass stopped after the first batch encountered blocking back-pressure without progress | `lotus-report` derived runtime state | `BatchRuntimePassResult.back_pressure_stopped` and `.back_pressure_reasons` | Available and used internally |
| `worker_process_id` | Stable daemonized worker identity recorded on item leases and worker logs | `lotus-report` worker configuration | `REPORT_BATCH_WORKER_ID` and `BatchWorkerProcessConfig.worker_id` | Available and used internally |
| `worker_process_interval_seconds` | Delay between daemonized worker passes | `lotus-report` worker configuration | `REPORT_BATCH_WORKER_INTERVAL_SECONDS` and `BatchWorkerProcess.run` | Available and used internally |
| `worker_process_max_batches_per_pass` | Maximum candidate batches advanced by one daemonized worker pass | `lotus-report` worker configuration | `REPORT_BATCH_WORKER_MAX_BATCHES_PER_PASS` and `BatchWorkerProcessConfig.max_batches_per_pass` | Available and used internally |
| `batch_schedule_id` | Governed schedule identity persisted into each scheduled batch option set | `lotus-report` scheduler configuration | `BatchScheduleDefinition.schedule_id` and `REPORT_BATCH_SCHEDULES_JSON` | Available and used internally |
| `batch_schedule_enabled` | Whether a configured schedule should be materialized during a scheduler pass | `lotus-report` scheduler configuration | `BatchScheduleDefinition.enabled` | Available and used internally |
| `batch_schedule_portfolio_ids` | Explicit portfolio ids selected for a configured scheduled batch | `lotus-report` scheduler configuration resolved through `lotus-core` | `BatchScheduleDefinition.portfolio_ids` and `CoreQueryClient.get_portfolio_detail` | Available and used internally |
| `batch_schedule_selector_mode` | Selector mode selected for a configured scheduled batch | `lotus-report` scheduler configuration | `BatchScheduleDefinition.selector_mode` | Available and used internally for explicit-list, all-active, and inline manifest schedules |
| `batch_schedule_manifest_entries` | Operator-authored inline manifest entries for a governed scheduled batch | `lotus-report` scheduler configuration with `lotus-core` eligibility verification | `BatchScheduleDefinition.manifest_entries`, `manifest_source`, `manifest_version`, and computed or supplied manifest hash | Available and used internally |
| `scheduler_process_id` | Stable scheduler identity used as scheduled batch caller identity and logs | `lotus-report` scheduler configuration | `REPORT_BATCH_SCHEDULER_ID` and `BatchSchedulerConfig.scheduler_id` | Available and used internally |
| `scheduler_process_interval_seconds` | Delay between daemonized scheduler passes | `lotus-report` scheduler configuration | `REPORT_BATCH_SCHEDULER_INTERVAL_SECONDS` and `BatchSchedulerProcess.run` | Available and used internally |
| `scheduler_process_materialized_batch_ids` | Durable batch ids created or idempotently reused by one scheduler pass | `lotus-report` derived runtime state | `BatchSchedulerRunResult.materialized` and scheduler pass logs | Available and used internally |
| `batch_id` | Opaque durable batch identifier for status and control APIs | `lotus-report` derived composition | `POST /reports/batches` response and `GET /reports/batches/{batch_id}` | Available and used |
| `status_url` | Relative URL for batch status lookup | `lotus-report` derived composition | `BatchHandleResponse.status_url`, `BatchControlResponse.status_url` | Available and used |
| `status_counts` | Product-safe count of batch items by item status | `lotus-report` derived composition | `BatchStatusResponse.status_counts` | Available and used |

Source constraints for later slices:

1. `all_active_portfolios` scheduler materialization uses the canonical `lotus-core /portfolios`
   discovery contract, filters active portfolios, and still relies on scheduler tenant/region
   configuration until RFC-0106 entitlement certification adds final tenant/region controls.
2. `batch_manifest` scheduler materialization supports inline governed schedule manifests with
   manifest source, version, entry source metadata, and content hash; upload-backed or
   reference-backed manifests remain future work.
3. Runtime pressure counts for upstream, render, and archive are accepted by the internal
   dispatcher as `BatchRuntimeLoad`; later worker/API slices must connect those counts to live
   runtime telemetry instead of static caller input.
4. Batch runtime execution is limited to internal bounded worker, runtime-pass, daemonized
   worker-process, and daemonized scheduler-process primitives plus the bounded single-batch
   `lotus-report` operator API. Gateway exposure and Workbench batch surface remain later slices.
