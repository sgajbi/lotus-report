# Operations Runbook

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
  handoff, batch worker passes, and scheduler passes.
- replay, rerender, regenerate, stuck-state, and SLA scan metrics are reserved until those command
  paths are implementation-backed.
- metrics must not use high-cardinality or sensitive labels such as client, portfolio, tenant,
  document, report job, batch, trace, correlation, storage, or raw payload fields.

## Observability vocabulary owner

- `src/app/observability.py` owns runtime correlation, request, trace, structured-log, and safe
  operator lookup field vocabulary.
- operator docs summarize those fields but must not introduce additional observability identifiers
  outside the code-owned vocabulary.
- RFC-0105 dashboards, replay, rerender, regenerate, and stuck-state APIs remain planned until
  implemented and proven with source-backed runtime evidence.

## Operational truths

- `lotus-report` composes from lotus-core, lotus-performance, and lotus-risk
- reporting payload quality depends on upstream fidelity and contract handling
- report job lifecycle state is durable in PostgreSQL and configured by
  `REPORT_JOB_LEDGER_DATABASE_URL`; runtime readiness fails if the database or mandatory ledger
  schema is unavailable
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
- Docker Compose uses `host.docker.internal` upstream URLs so the container can reach the
  host-published canonical upstream ports while callers continue to use `report.dev.lotus`
- Docker Compose starts a separate `lotus-report-postgres` service for local report job ledger
  parity; do not use a file database for runtime or integration evidence

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
- broad replay, rerender, regenerate, or document distribution controls

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
5. cancellation is bounded to pre-render/pre-archive/pre-completion jobs,
6. every report job has one durable `report_request`, one durable `report_job`, and append-only
   `report_status_event` rows.

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
retrieval, rerender, reissue, and archive housekeeping belong to `lotus-archive` or later
reporting architecture RFCs. `lotus-report` records only the archive handoff request id, document
id, completion timestamp, and truthful archive failure posture.

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
  -H "Content-Type: application/json" `
  -H "Idempotency-Key: portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22" `
  -H "X-Actor-Id: advisor-123" `
  -H "X-Caller-Application: lotus-workbench" `
  -H "X-Tenant-Id: tenant-sg" `
  -H "X-Region: APAC" `
  -H "X-Booking-Center-Code: SG" `
  -H "X-Role: advisor" `
  -H "X-Correlation-ID: portfolio-review-job-local-proof" `
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

## Key references

- [docs/standards/data-model-ownership.md](../docs/standards/data-model-ownership.md)
- [docs/standards/enterprise-readiness.md](../docs/standards/enterprise-readiness.md)
- [docs/standards/migration-contract.md](../docs/standards/migration-contract.md)
- [docs/standards/scalability-availability.md](../docs/standards/scalability-availability.md)
- [Portfolio Review Report](Portfolio-Review-Report)
