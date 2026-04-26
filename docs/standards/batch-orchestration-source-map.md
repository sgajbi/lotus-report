# Batch Orchestration Source Map

RFC-0104 batch materialization is source-backed. Slice 2 persists batch and batch-item truth.
Slice 3 adds deterministic schedule-cycle materialization and scheduled-batch identity primitives.
Slice 4 adds internal dispatch, lease, and back-pressure primitives. APIs, scheduler loops, retry,
and recovery remain later slices.

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

Source gaps for later slices:

1. `all_active_portfolios` needs a certified `lotus-core` portfolio search contract with tenant,
   region, active status, entitlement, and maximum-size controls.
2. `batch_manifest` needs a governed manifest upload or manifest-reference contract before it can
   be materialized.
3. Runtime pressure counts for upstream, render, and archive are accepted by the internal
   dispatcher as `BatchRuntimeLoad`; later worker/API slices must connect those counts to live
   runtime telemetry instead of static caller input.
