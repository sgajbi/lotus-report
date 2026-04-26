# Batch Orchestration Source Map

RFC-0104 batch materialization is source-backed. Slice 2 intentionally persists only batch and
batch-item truth. Slice 3 adds deterministic schedule-cycle materialization and scheduled-batch
identity primitives. APIs, scheduler loops, dispatch, retry, and recovery remain later slices.

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

Source gaps for later slices:

1. `all_active_portfolios` needs a certified `lotus-core` portfolio search contract with tenant,
   region, active status, entitlement, and maximum-size controls.
2. `batch_manifest` needs a governed manifest upload or manifest-reference contract before it can
   be materialized.
3. Dispatch to `report_job` is intentionally deferred to a later runtime slice, so current
   schedule materialization does not create report jobs.
