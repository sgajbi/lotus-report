# Operations Runbook

Current scope: operator-facing checks, support-safe evidence paths, and copy-paste command examples
for the implementation-backed `lotus-report` runtime.

## First Response Matrix

| Situation | First action | Evidence path |
| --- | --- | --- |
| Service readiness concern | Check health/readiness and PostgreSQL-backed ledger posture | `/health/ready`, migration smoke, indexed ledger queries |
| One report job needs support review | Use product-safe status, events, diagnostics, snapshot, and lineage paths | `GET /api/v1/report-jobs/{job_id}`, `/events`, direct diagnostics |
| Failed report or batch work needs intervention | Use rerender, regenerate, failed-work replay, batch control, or run-once only when eligibility rules match | caller-context config plus operation-specific idempotency where required |

## Important operational checks

- confirm canonical reporting identity is `report.dev.lotus` before cross-app validation
- treat upstream client failures as reporting-orchestration issues first, not as local formatting bugs
- verify correlation, request, trace, and observability headers on reporting endpoints
- use repo-native gates before inventing ad hoc checks
- for portfolio review evidence, verify the JSON contract, section readiness, report coverage,
  advisor-only separation, AI-readiness guardrails, and evidence lineage before treating the output
  as meeting-ready
- for report job evidence, verify idempotency behavior, operator-safe search, product-safe status,
  append-only status events, durable snapshot evidence, upstream lineage evidence, render metadata,
  and bounded cancellation before `rendering`/archive/completion

## Health and readiness surfaces

- `/health`
  broad service-health probe
- `/health/live`
  liveness probe
- `/health/ready`
  readiness probe for traffic acceptance
- `/metrics`
  observability surface for runtime monitoring

## RFC-0105 metrics contract

- `docs/operations/reporting-observability-metrics.md` records the code-backed first-wave metrics
  contract, dashboard contract, alert basis, and label restrictions.
- implemented metrics cover report job submission, snapshot capture, render handoff, archive
  handoff, rerender-from-snapshot, regenerate-from-upstream, failed-work replay commands, batch
  worker passes, scheduler passes, and operations attention scans.
- dedicated broader replay dashboards remain reserved until those command paths are
  implementation-backed.
- metrics must not use high-cardinality or sensitive labels such as client, portfolio, tenant,
  document, report job, batch, trace, correlation, storage, or raw payload fields.

## Observability vocabulary owner

- `src/app/observability.py` owns runtime correlation, request, trace, structured-log, and safe
  operator lookup field vocabulary.
- operator docs summarize those fields but must not introduce additional observability identifiers
  outside the code-owned vocabulary.
- Use `GET /reports/operations/attention` for the implementation-backed stuck-state and SLA-breach
  attention scan. Dedicated replay dashboards remain planned until implemented and proven with
  source-backed runtime evidence.

## Operational truths

- `lotus-report` composes from lotus-core, lotus-performance, and lotus-risk
- reporting payload quality depends on upstream fidelity and contract handling
- report job lifecycle state is durable in PostgreSQL and configured by
  `REPORT_JOB_LEDGER_DATABASE_URL`; runtime readiness fails if the database or mandatory ledger
  schema is unavailable
- report-job, report-batch, and snapshot/upstream-call PostgreSQL adapters share a bounded
  process-local connection provider configured by `REPORT_POSTGRES_POOL_MIN_SIZE`,
  `REPORT_POSTGRES_POOL_MAX_SIZE`, `REPORT_POSTGRES_POOL_ACQUIRE_TIMEOUT_SECONDS`,
  `REPORT_POSTGRES_CONNECT_TIMEOUT_SECONDS`, `REPORT_POSTGRES_STATEMENT_TIMEOUT_MS`, and
  `REPORT_POSTGRES_APPLICATION_NAME`; tune the pool before increasing API, worker, or scheduler
  concurrency
- RFC-0101 extends readiness to fail when either `report_input_snapshot` or
  `report_upstream_call` schema is unavailable
- report job support queries are backed by indexes for idempotency lookup, tenant/region/time
  diagnostics, as-of-date filtering, portfolio-scope diagnostics, status queues, completion scans,
  request/job joins, append-only event history, snapshot lookup, and upstream-lineage lookup
- native PostgreSQL partitioning is intentionally deferred until a later scale/retention RFC can
  preserve global idempotency semantics; the current ledger is partition-ready but not partitioned
- destructive purge, legal hold, and document-retention operations are not first-wave ledger
  features and must not be simulated with manual deletes in support workflows
- direct process port `8300` is useful for local debugging, but canonical cross-app validation
  should use `report.dev.lotus`
- direct local debugging must set `ENTERPRISE_RUNTIME_PROFILE=local`; production-like profiles
  (`prod`, `production`, `preprod`, `staging`, and `uat`) enforce read and write authorization
  even when authz toggles are omitted
- production-like direct service startup requires `ENTERPRISE_ENFORCE_AUTHZ=true`,
  `ENTERPRISE_ENFORCE_READ_AUTHZ=true`, and `ENTERPRISE_PRIMARY_KEY_ID`; otherwise the service
  raises `enterprise_runtime_config_invalid` during startup validation
- Docker Compose uses `host.docker.internal` upstream URLs so the container can reach the
  host-published canonical upstream ports while callers continue to use `report.dev.lotus`
- Docker Compose starts a separate `lotus-report-postgres` service for local report job ledger
  parity; do not use a file database for runtime or integration evidence
- Docker Compose containers initialize the PostgreSQL report-job ledger and report-input snapshot
  migrations before serving the API, worker, or scheduler process. `/health/ready` must remain 503
  when those tables are absent, and a fresh volume should become ready without manual SQL.

## RFC-0104 batch reporting posture

RFC-0104 first-wave batch support is implemented for internal `lotus-report`
materialization/status/control operations. Certified APIs exist for:

- `POST /reports/batches`
- `GET /reports/batches/{batch_id}`
- `POST /reports/batches/{batch_id}:pause`
- `POST /reports/batches/{batch_id}:resume`
- `POST /reports/batches/{batch_id}:cancel`
- `POST /reports/batches/{batch_id}:retry-failed`
- `POST /reports/batches/{batch_id}:recover-expired-leases`
- `POST /reports/batches/{batch_id}:run-once`

Current implemented semantics:

- batch creation is idempotent through `Idempotency-Key`
- explicit portfolio lists and selected subsets are supported when source-backed eligible
  candidates are provided
- batch status returns product-safe item summaries, lifecycle timestamps, status counts,
  correlation id, and trace id
- pause/resume/cancel/retry/recovery controls operate on the durable batch ledger and preserve
  already-created report jobs
- internal dispatch can lease batch items, create or reuse one report job per item, and apply
  active-batch, active-item, upstream, render, and archive back-pressure
- internal item execution can advance a dispatched item through the existing report-job, snapshot,
  render, and archive handoff path, then reconcile final item state
- bounded `run-once` operator calls can recover expired pre-dispatch leases, dispatch eligible
  items, and advance already waiting items for one explicit batch; the response returns safe counts,
  linked report job ids, back-pressure reasons, skip reasons, and per-item execution outcomes
- internal runtime passes can scan durable runnable batches and invoke the single-batch worker for
  a limited number of batches; this is a service primitive only and is not a public API or daemon
- the `lotus-report-batch-worker` Docker Compose service runs the bounded runtime pass as a
  daemonized internal background worker process under configured interval, batch-count, lease, and
  back-pressure limits
- the `lotus-report-batch-scheduler` Docker Compose service reads governed
  `REPORT_BATCH_SCHEDULES_JSON`, verifies explicit, all-active, and inline manifest schedule
  selectors through `lotus-core` or governed schedule manifest metadata, and materializes durable
  idempotent scheduled batches for the worker process to execute
- `GET /reports/batch-schedules` lists the configured schedules, and
  `POST /reports/batch-schedules:run-due` runs one bounded scheduler materialization pass over
  enabled schedules without executing batch items

Still not supported:

- Workbench scheduler-management surface
- schedule CRUD or persisted scheduler registry management
- entitlement-certified public scheduler runtime
- broad replay or document distribution controls

Use individual report-job APIs for production portfolio-review initiation until later RFC-0104
slices ship the remaining scheduler-management surfaces. Use `lotus-report` batch APIs and internal
worker/scheduler services only for the certified materialization/status/control/run-once,
config-backed scheduler-administration, and service-runtime subset.

Observability floor for this wave:

- every batch API requires caller context headers and carries correlation/trace identifiers into
  durable batch state
- report-to-render submission now forwards the report job correlation and trace identifiers to
  `lotus-render`; archive handoff already forwards both identifiers to `lotus-archive`
- status responses expose product-safe failure category and summary without SQL or raw stack traces
- readiness remains database-aware through `/health/ready`
- PostgreSQL-backed proof is required for batch runtime and recovery behavior; SQLite is only a
  unit-test adapter
- RFC-0105 remains responsible for richer operational dashboards, replay tooling, alert policy,
  and long-running batch runtime telemetry

## RFC-0100 gateway-first job flow

Front-office report job initiation is gateway-first. Workbench and other product surfaces should
call `lotus-gateway`; `lotus-report` remains the durable job owner and internal orchestration
service.

```mermaid
sequenceDiagram
    participant WB as lotus-workbench / caller
    participant GW as lotus-gateway
    participant REPORT as lotus-report
    participant PG as lotus-report-postgres
    participant RENDER as lotus-render
    participant ARCHIVE as lotus-archive

    WB->>GW: POST /api/v1/reports/portfolio-reviews
    GW->>REPORT: POST /reports/portfolio-reviews
    REPORT->>PG: insert report_request, report_job, report_status_event
    REPORT-->>GW: 202 job handle
    GW-->>WB: 202 product-safe job handle
    WB->>GW: GET /api/v1/report-jobs?portfolioId=...
    GW->>REPORT: GET /reports/jobs?portfolioId=...
    REPORT->>PG: indexed support search
    REPORT-->>GW: bounded support-safe job summaries
    WB->>GW: GET /api/v1/report-jobs/{job_id}
    GW->>REPORT: GET /reports/jobs/{job_id}
    REPORT->>PG: read current job and request context
    REPORT-->>GW: product-safe status
    WB->>GW: GET /api/v1/report-jobs/{job_id}/events
    GW->>REPORT: GET /reports/jobs/{job_id}/events
    REPORT->>PG: read append-only status events
    REPORT-->>GW: event history
    WB->>GW: POST /api/v1/report-jobs/{job_id}/cancel
    GW->>REPORT: POST /reports/jobs/{job_id}/cancel
    REPORT->>PG: mark job cancelled and append cancellation event
```

Required caller context for job creation:

1. `Idempotency-Key`
2. `X-Actor-Id`
3. `X-Caller-Application`
4. `X-Tenant-Id`
5. `X-Region`
6. `X-Booking-Center-Code`
7. `X-Role`
8. `X-Correlation-ID`
9. distributed trace context through `traceparent` or `X-Trace-ID`

Use this reusable curl config for direct `lotus-report` and gateway support commands. Keep
operation-specific `Idempotency-Key` headers inline on mutations that require duplicate-safe retry
semantics.

```text
file: report-operator-headers.curl
header = "X-Actor-Id: support-operator-1"
header = "X-Caller-Application: lotus-report-ops"
header = "X-Tenant-Id: tenant-sg"
header = "X-Region: APAC"
header = "X-Booking-Center-Code: SG"
header = "X-Role: support_operator"
header = "X-Correlation-ID: report-operator-local-proof"
header = "X-Trace-ID: trace-report-operator-local-proof"
```

Expected controls:

1. a duplicate request with the same `Idempotency-Key` and same canonical request hash returns the
   existing job handle,
2. a duplicate `Idempotency-Key` with a different canonical request hash returns `409
   idempotency_conflict`,
3. search, status, event, and RFC-0105 diagnostics endpoints return product-safe diagnostics and no
   database internals,
4. `GET /reports/jobs/{job_id}/diagnostics` is the first stop for one-job operator review because
   it composes source-backed status, latest event, snapshot posture, upstream-lineage summary,
   render metadata, archive handoff identifiers, and evidence links without raw payloads,
5. `POST /reports/jobs/{job_id}/rerender` is only for archived PDF jobs and creates a new
   rerender attempt from the existing immutable snapshot without recollecting upstream data,
6. `POST /reports/jobs/{job_id}/regenerate` is only for archived PDF jobs and creates a new report
   job, fresh upstream snapshot and lineage bundle, and replacement archive document when source
   data must be refreshed,
7. `POST /reports/jobs/{job_id}/replay` is only for failed retry-eligible report jobs and creates
   or reuses a replay-scoped report job without duplicating completed archived documents,
8. `POST /reports/batches/{batch_id}/items/{batch_item_id}/replay` is only for failed
   retry-eligible implementation-backed batch items linked to failed report jobs; it relinks the
   item to replay work and does not change scheduler configuration,
9. cancellation is bounded to pre-render/pre-archive/pre-completion jobs,
10. every report job has one durable `report_request`, one durable `report_job`, and append-only
   `report_status_event` rows.

Use rerender for presentation, template, or rendering corrections where the source snapshot remains
authoritative. Use regenerate when upstream domain data was corrected, late, or incomplete and the
operator needs a replacement document backed by a new lineage bundle. Use failed-work replay only
when the source job or implementation-backed batch item failed before producing a completed archive
document. These commands require `Idempotency-Key` and caller context headers, and they do not
expose raw snapshot payloads, storage keys, or upstream response bodies.

## RFC-0101 snapshot and lineage flow

RFC-0101 adds durable evidence capture on top of the RFC-0100 job ledger. The first wave is still
owned by `lotus-report`; gateway remains an ingress and status boundary, not the durable evidence
owner.

```mermaid
sequenceDiagram
    participant GW as lotus-gateway or operator caller
    participant REPORT as lotus-report
    participant CORE as lotus-core
    participant PERF as lotus-performance
    participant RISK as lotus-risk
    participant PG as lotus-report-postgres

    GW->>REPORT: POST /reports/portfolio-reviews
    REPORT->>PG: insert report_request, report_job, accepted event
    REPORT->>PG: mark job collecting_data
    REPORT->>CORE: summary/detail/allocation/positions/transactions calls
    REPORT->>PERF: workspace summary and contribution calls
    REPORT->>RISK: risk calculation calls
    REPORT->>PG: insert report_input_snapshot
    REPORT->>PG: insert report_upstream_call rows
    REPORT->>PG: mark job data_ready or failed
    REPORT->>PG: mark job rendering
    REPORT->>RENDER: POST /renders
    RENDER-->>REPORT: rendered artifact metadata or failure
    REPORT->>PG: mark job completed or failed
    REPORT->>PG: mark job archiving
    REPORT->>ARCHIVE: POST /documents
    ARCHIVE-->>REPORT: archive document id or failure
    REPORT->>PG: mark job archived or failed
    REPORT-->>GW: 202 job handle with current status
```

Operational truths for this wave:

1. one report job has zero or one durable snapshot,
2. one snapshot can have many upstream-call rows,
3. snapshot rows are immutable from the application contract perspective,
4. upstream-call rows are append-only from the application contract perspective,
5. support-safe APIs return hashes, posture, and lineage metadata instead of raw upstream payloads.

## PostgreSQL ledger operations

The local parity database is the `lotus-report-postgres` container published on host port `5439`.
Runtime and integration evidence should set:

```powershell
$env:REPORT_JOB_LEDGER_DATABASE_URL = "postgresql://lotus_report:lotus_report@localhost:5439/lotus_report"
```

Operator-safe inspection queries should target indexed paths:

```sql
-- request lineage by idempotency key
SELECT report_request_id, report_type, portfolio_scope_json, as_of_date, triggered_by,
       caller_application, tenant_id, region, booking_center_code, role,
       idempotency_key, request_hash, correlation_id, trace_id, created_at
FROM report_request
WHERE idempotency_key = '<idempotency-key>';

-- current job state for support triage
SELECT job.report_job_id, job.report_request_id, job.status, job.failure_category,
       job.current_step, job.retry_eligible, job.cancel_requested,
       job.created_at, job.updated_at, job.cancelled_at
FROM report_job job
JOIN report_request req ON req.report_request_id = job.report_request_id
WHERE req.idempotency_key = '<idempotency-key>';

-- append-only lifecycle evidence
SELECT event.status_event_id, event.report_job_id, event.from_status, event.to_status,
       event.event_type, event.actor, event.correlation_id, event.trace_id, event.created_at
FROM report_status_event event
JOIN report_job job ON job.report_job_id = event.report_job_id
JOIN report_request req ON req.report_request_id = job.report_request_id
WHERE req.idempotency_key = '<idempotency-key>'
ORDER BY event.created_at;

-- durable snapshot evidence for one job
SELECT snapshot_id, report_job_id, report_type, report_data_contract_version, as_of_date,
       snapshot_hash, snapshot_storage_ref, supportability_status, completeness_status,
       lineage_summary_json, captured_at, correlation_id, trace_id
FROM report_input_snapshot
WHERE report_job_id = '<report-job-id>';

-- append-only upstream lineage for one snapshot
SELECT upstream_call_id, snapshot_id, service_name, endpoint, method, contract_version,
       request_hash, response_hash, response_ref, status_code, latency_ms,
       supportability_status, completeness_status, failure_category, failure_message,
       correlation_id, trace_id, captured_at
FROM report_upstream_call
WHERE snapshot_id = '<snapshot-id>'
ORDER BY captured_at, upstream_call_id;
```

Do not manually delete ledger rows to clean a failed test. Use isolated idempotency keys and keep
ledger rows as audit evidence. Native partitioning, purge, legal hold, document retention,
retrieval, broad replay, archive reissue, and archive housekeeping belong to `lotus-archive` or
later reporting architecture RFCs. `lotus-report` records the archive handoff request id, document
id, completion timestamp, truthful archive failure posture, rerender correction attempts, and
regenerate replacement attempts for already archived PDF reports.

## Practical probes

```powershell
curl http://127.0.0.1:8300/health/ready
curl "http://127.0.0.1:8300/aggregations/portfolios/DEMO_DPM_EUR_001?as_of_date=2026-02-24&live=false"
```

Portfolio review proof:

```powershell
curl -X POST "http://127.0.0.1:8300/reports/portfolios/PB_SG_GLOBAL_BAL_001/review?section_limit=20" `
  -H "Content-Type: application/json" `
  -H "X-Correlation-ID: portfolio-review-local-proof" `
  -d "{\"as_of_date\":\"2026-04-22\",\"reporting_currency\":\"USD\",\"benchmark_code\":\"BMK_PB_GLOBAL_BALANCED_60_40\",\"sections\":[\"CLIENT_PROFILE\",\"OVERVIEW\",\"ALLOCATION\",\"PERFORMANCE\",\"RISK_ANALYTICS\",\"INCOME_AND_ACTIVITY\",\"HOLDINGS\",\"TRANSACTIONS\"]}"
```

Expected posture:

1. `client_profile.status` should show sourced profile state or explicit missing fields.
2. `key_figures` should include portfolio, allocation, performance, contribution, risk,
   holdings/P&L, activity, and client-profile families where sourced.
3. `report_coverage` should mark unsupported enterprise-grade families as `not_sourced` instead of
   silently omitting them.
4. `advisor_briefing` should stay deterministic and advisor-only.
5. `ai_readiness` should describe guarded assistance and blocked advice/suitability use cases.

Portfolio review report job proof:

```powershell
curl -X POST "http://gateway.dev.lotus:8111/api/v1/reports/portfolio-reviews" `
  --config report-operator-headers.curl `
  -H "Content-Type: application/json" `
  -H "Idempotency-Key: portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22" `
  -d "{\"portfolio_scope\":{\"portfolio_ids\":[\"PB_SG_GLOBAL_BAL_001\"]},\"as_of_date\":\"2026-04-22\",\"requested_output_formats\":[\"pdf\"],\"reporting_currency\":\"USD\",\"options\":{\"sections\":[\"OVERVIEW\",\"PERFORMANCE\",\"RISK_ANALYTICS\"],\"benchmark_code\":\"BMK_PB_GLOBAL_BALANCED_60_40\"}}"
```

The expected gateway response is a job handle with `report_request_id`, `report_job_id`, `status`,
`status_url`, and `idempotency_key`. JSON-only requests typically advance the handle to
`data_ready` before the response returns. PDF requests may continue through `rendering`,
`completed`, `archiving`, and `archived` before the response returns when render and archive
handoffs succeed synchronously. Use
`GET /api/v1/report-jobs?portfolioId=...&tenantId=...` for support search, `GET /api/v1/report-jobs/{job_id}` for status, and
`GET /api/v1/report-jobs/{job_id}/events` for append-only lifecycle diagnostics. Use
`POST /api/v1/report-jobs/{job_id}/cancel` only before the job reaches `rendering`, archive, or completion.

When testing `lotus-report` directly for service-owned diagnostics, use the equivalent internal
paths `POST /reports/portfolio-reviews`, `GET /reports/jobs`, `GET /reports/jobs/{job_id}`,
`GET /reports/jobs/{job_id}/events`, `GET /reports/jobs/{job_id}/snapshot`,
`GET /reports/jobs/{job_id}/lineage`, `GET /reports/snapshots/{snapshot_id}`,
`GET /reports/snapshots/{snapshot_id}/lineage`, and `POST /reports/jobs/{job_id}/cancel`.

Reusable operator command examples:

Job status, events, and support-safe reads:

```powershell
curl "http://gateway.dev.lotus:8111/api/v1/report-jobs/rjob_example" `
  --config report-operator-headers.curl

curl "http://gateway.dev.lotus:8111/api/v1/report-jobs/rjob_example/events" `
  --config report-operator-headers.curl

curl "http://127.0.0.1:8300/reports/jobs/rjob_example/diagnostics" `
  --config report-operator-headers.curl

curl "http://127.0.0.1:8300/reports/jobs/rjob_example/snapshot" `
  --config report-operator-headers.curl

curl "http://127.0.0.1:8300/reports/jobs/rjob_example/lineage" `
  --config report-operator-headers.curl
```

Job cancellation and correction commands:

```powershell
curl -X POST "http://gateway.dev.lotus:8111/api/v1/report-jobs/rjob_example/cancel" `
  --config report-operator-headers.curl

curl -X POST "http://127.0.0.1:8300/reports/jobs/rjob_example/rerender" `
  --config report-operator-headers.curl `
  -H "Content-Type: application/json" `
  -H "Idempotency-Key: rerender-rjob_example-v1" `
  -d "{\"reason\":\"correct_template_or_presentation\"}"

curl -X POST "http://127.0.0.1:8300/reports/jobs/rjob_example/regenerate" `
  --config report-operator-headers.curl `
  -H "Content-Type: application/json" `
  -H "Idempotency-Key: regenerate-rjob_example-v1" `
  -d "{\"reason\":\"refresh_corrected_upstream_data\"}"

curl -X POST "http://127.0.0.1:8300/reports/jobs/rjob_failed_example/replay" `
  --config report-operator-headers.curl `
  -H "Content-Type: application/json" `
  -H "Idempotency-Key: replay-rjob_failed_example-v1" `
  -d "{\"reason\":\"retry_failed_work\"}"
```

Batch materialization and status:

```powershell
curl -X POST "http://127.0.0.1:8300/reports/batches" `
  --config report-operator-headers.curl `
  -H "Content-Type: application/json" `
  -H "Idempotency-Key: batch-PB_SG_GLOBAL_BAL_001-2026-04-22" `
  -d "{\"selector_mode\":\"explicit_portfolio_list\",\"portfolio_ids\":[\"PB_SG_GLOBAL_BAL_001\"],\"source_candidates\":[{\"portfolio_id\":\"PB_SG_GLOBAL_BAL_001\",\"tenant_id\":\"tenant-sg\",\"region\":\"APAC\",\"active\":true,\"selected\":true,\"source_system\":\"lotus-core\",\"source_object\":\"PortfolioScope\"}],\"as_of_date\":\"2026-04-22\",\"requested_output_formats\":[\"pdf\"],\"reporting_currency\":\"USD\"}"

curl "http://127.0.0.1:8300/reports/batches/rbatch_example" `
  --config report-operator-headers.curl
```

Batch controls:

```powershell
curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example:pause" `
  --config report-operator-headers.curl

curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example:resume" `
  --config report-operator-headers.curl

curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example:cancel" `
  --config report-operator-headers.curl

curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example:retry-failed" `
  --config report-operator-headers.curl

curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example:recover-expired-leases" `
  --config report-operator-headers.curl
```

Batch item replay and bounded run-once:

```powershell
curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example/items/rbci_failed_example/replay" `
  --config report-operator-headers.curl `
  -H "Content-Type: application/json" `
  -H "Idempotency-Key: batch-item-replay-rbci_failed_example-v1" `
  -d "{\"reason\":\"retry_failed_batch_item\"}"

curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example:run-once" `
  --config report-operator-headers.curl `
  -H "Content-Type: application/json" `
  -d "{\"worker_id\":\"lotus-report-batch-worker-1\",\"recover_expired_leases\":true,\"dispatch_policy\":{\"max_active_batches\":1,\"max_active_items\":5,\"max_active_upstream_jobs\":3,\"max_active_render_jobs\":2,\"max_active_archive_jobs\":2,\"lease_seconds\":300},\"runtime_load\":{\"active_batches\":0,\"active_items\":0,\"active_upstream_jobs\":0,\"active_render_jobs\":0,\"active_archive_jobs\":0}}"
```

## Key references

- [docs/standards/data-model-ownership.md](../docs/standards/data-model-ownership.md)
- [docs/standards/enterprise-readiness.md](../docs/standards/enterprise-readiness.md)
- [docs/standards/migration-contract.md](../docs/standards/migration-contract.md)
- [docs/standards/scalability-availability.md](../docs/standards/scalability-availability.md)
- [Portfolio Review Report](Portfolio-Review-Report)
