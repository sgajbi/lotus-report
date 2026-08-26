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
- implemented metrics cover report job submission, report-job worker passes, snapshot capture,
  render handoff, archive handoff, rerender-from-snapshot, regenerate-from-upstream, failed-work
  replay commands, report-work lease recovery/exhaustion/stale-conflict outcomes, batch worker
  passes, scheduler passes, and operations attention scans.
- dedicated broader replay dashboards remain reserved until those command paths are
  implementation-backed.
- metrics must not use high-cardinality or sensitive labels such as client, portfolio, tenant,
  document, report job, batch, worker, lease token, trace, correlation, storage, or raw payload
  fields.

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
- report-job, report-work, report-batch, and snapshot/upstream-call PostgreSQL adapters share a bounded
  process-local connection provider configured by `REPORT_POSTGRES_POOL_MIN_SIZE`,
  `REPORT_POSTGRES_POOL_MAX_SIZE`, `REPORT_POSTGRES_POOL_ACQUIRE_TIMEOUT_SECONDS`,
  `REPORT_POSTGRES_CONNECT_TIMEOUT_SECONDS`, `REPORT_POSTGRES_STATEMENT_TIMEOUT_MS`, and
  `REPORT_POSTGRES_APPLICATION_NAME`; tune the pool before increasing API, report-job worker,
  batch-worker, or scheduler concurrency
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
- Docker Compose containers initialize the PostgreSQL report-job ledger, work queue, and report-input
  snapshot migrations before serving the API, report-job worker, batch worker, or scheduler process.
  `/health/ready` must remain 503
  when those tables are absent. Both a fresh volume and the supported
  `report-status-event-pre-contract-v0` schema should become ready without manual SQL or volume
  deletion.

## Schema Upgrade And Startup Recovery

Run the governed PostgreSQL proof before diagnosing a service-specific failure:

```powershell
$env:REPORT_JOB_LEDGER_DATABASE_URL="postgresql://lotus_report:lotus_report@localhost:5439/lotus_report"
make migration-smoke
```

`make migration-smoke` validates the current schema and runs an isolated, repeatable upgrade from
`report-status-event-pre-contract-v0` to `report-ledger-v1`. The isolated check preserves the
configured database's `public` schema and verifies legacy event identity, message, correlation,
trace, contract defaults, and required indexes after migration.

If any Report container exits with
`lotus_report_schema_startup_failed:report_schema_upgrade_unsupported`:

1. preserve the PostgreSQL volume and capture the complete stable diagnostic;
2. stop the API, report-job worker, batch worker, and scheduler from repeatedly attempting startup;
3. compare the named missing or incompatible columns with
   `docs/standards/migration-contract.md`;
4. use an approved forward-fix or restore path for unsupported shapes;
5. rerun `make migration-smoke`, then start the API, report-job worker, batch worker, and scheduler
   against the same volume;
6. confirm `/health/ready` returns `200` before resuming Gateway or Workbench validation.

Do not repair this condition with manual column changes, destructive volume removal, or a
Workbench fallback. Those paths can lose report history or make the browser advertise readiness
that the reporting service has not proved.

## Durable Report-Job Worker

`lotus-report-job-worker` is the only daemonized owner of newly accepted report-job execution. The
API must persist one `report_job_work_item` in the same transaction as the accepted job and return
without waiting for source capture, render, or archive latency.

Governed controls are:

- `REPORT_JOB_WORKER_ID`: stable lease-owner identity for one worker instance;
- `REPORT_JOB_WORKER_INTERVAL_SECONDS`: delay between bounded passes;
- `REPORT_JOB_WORKER_MAX_ITEMS_PER_PASS`: maximum sequential claim-and-execute cycles in one pass;
- `REPORT_JOB_WORKER_LEASE_SECONDS`: lease duration before another worker may recover abandoned work;
- `REPORT_JOB_WORKER_MAX_ATTEMPTS`: total bounded execution attempts;
- `REPORT_JOB_WORKER_RETRY_BASE_SECONDS` and `REPORT_JOB_WORKER_RETRY_MAX_SECONDS`: bounded
  exponential retry window.

Scale workers only after confirming PostgreSQL pool capacity and downstream Core, Performance,
Risk, Render, and Archive limits. A worker claims one row with PostgreSQL `SKIP LOCKED` immediately
before executing it; `MAX_ITEMS_PER_PASS` bounds sequential throughput rather than pre-leasing a
batch. A lease token, not worker identity alone, proves completion or failure ownership. An
interrupted worker leaves the work item recoverable after lease expiry. Expiry consumes the same
bounded attempt policy as explicit execution failure: a retryable item observes exponential delay
from the lease deadline, while final exhaustion atomically fails the work item and source report
job with a replay-eligible lifecycle event. The execution pipeline resumes from persisted job state
and must not append a duplicate transition for an already completed stage.

For an accepted job that is not progressing:

1. read the product-safe job status and lifecycle events;
2. inspect worker logs, `lotus_report_job_runtime_last_items`, and
   `lotus_report_job_work_lease_events_total{outcome=~"recovered|exhausted|stale_conflict"}` without
   adding worker, portfolio, tenant, job, lease-token, correlation, or trace identifiers as labels;
3. verify the work item is `pending`, `retry_pending`, or has an expired `leased` state;
4. verify downstream source health before increasing retry or concurrency settings;
5. restart only the report-job worker when the lease is abandoned; when the bounded policy has
   exhausted, inspect the source-owned failed status and use governed replay only after the
   downstream cause is corrected;
6. do not recreate the request, delete the work row, or reset the Report volume.

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
- batch and item status return product-safe summaries, lifecycle timestamps, status counts,
  correlation id, trace id, the linked Report job state, and the source-owned archive document id
  only after that exact job reaches `archived`
- an `archive_document_id` of null means the output is not openable from batch status: the item may
  be unlinked, rendering or archiving may still be in progress, the job may have failed or been
  cancelled, or a legacy/inconsistent job link may be unresolved; use `report_job_status` to
  distinguish those cases and never derive a document id from batch, item, portfolio, or job ids
- replay relinks the item to the replay-scoped report job, so later status follows only that job;
  corrections and replacements do not silently replace the batch reference and must be resolved
  through the governed Archive metadata and report-job diagnostics boundaries
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
- a runtime pass is scoped to one tenant: it selects only batches whose persisted
  `tenant_id` matches `REPORT_BATCH_WORKER_TENANT_ID`, and the worker refuses any batch outside
  that tenant
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

Tenant scope of the background worker:

- one `lotus-report-batch-worker` process advances batches for exactly one tenant, named by
  `REPORT_BATCH_WORKER_TENANT_ID`. Operating more than one tenant means running one worker
  process per governed tenant, each with its own value.
- a batch whose tenant has no running worker is **not advanced**. It stays materialized and
  visible through the status API; it does not fail and nothing is lost. Symptom to expect if a
  tenant's worker is missing or misconfigured: batches for that tenant sit at `materialized` with
  no linked report jobs while other tenants progress normally.
- to diagnose, compare `REPORT_BATCH_WORKER_TENANT_ID` on each running worker against the
  `tenant_id` of the stalled batch from `GET /reports/batches/{batch_id}`. Start or correct the
  worker for that tenant; no batch state needs repair.
- this is deliberate. Before it, one worker scanned every tenant's runnable batches and dispatched
  them under its own configured tenant, so a batch belonging to one tenant produced report jobs
  owned by another. Not advancing a batch is recoverable; advancing it into the wrong tenant is
  not.

Batch status reads are tenant-scoped through the link, not only at the batch:

- `GET /reports/batches/{batch_id}` and the item-status route resolve linked report jobs with the
  admitted batch tenant, so a job belonging to another tenant is not returned by the lookup at all.
  Its lifecycle status and `archive_document_id` therefore cannot appear in either response.
- the effect on a cross-linked item is that it reads as **unlinked**: `report_job_status` and
  `archive_document_id` are `null`. That is deliberate - the caller owns the batch, so the response
  is not an error, but nothing is disclosed about the foreign job. An item stuck in that shape is a
  data defect and shows up as a quarantine on the execution path below.

Quarantined batch items (`batch_item_tenant_mismatch`):

- before executing a dispatched item, the execution bridge compares the persisted tenant of the
  **linked report job** with the tenant of its batch. If they differ the item is refused, marked
  `failed_terminal` with error category `batch_item_tenant_mismatch`, and never retried. No
  snapshot, render, or archive work is started.
- the same comparison runs on **batch-item replay**. The rule there is *observe always, mutate only
  for a request that would otherwise have acted*:
  - the mismatch is **always logged**, whatever the item's state. A terminal item carrying a foreign
    link is the **stronger** signal, not a weaker one — the dispatch that wrote the link already
    happened, so a report exists against another tenant's job. The log line carries `item_status`
    and `quarantined` so the two cases are distinguishable.
  - the item is **quarantined only** when replay would otherwise have acted on it
    (`waiting_on_report_job`, or `failed_retryable` while still retry-eligible). Rewriting a
    `succeeded` item would destroy finished work in response to a call that was never going to
    change anything, and could flip its batch to `completed_with_failures`.
  - a **malformed** request — a missing or blank `Idempotency-Key` — is refused before any of this,
    so it cannot quarantine anything.
  - **searching for mismatches**: grep the worker and API logs for `batch_item_tenant_mismatch`
    rather than relying on the quarantine category alone, because the items with the strongest
    evidence are exactly the ones that are logged but **not** quarantined. A caller who legitimately owns the batch, but
  whose item is linked to another tenant's report job, receives the ordinary
  `409 report_batch_item_cannot_be_replayed` contract - true, and disclosing nothing about the other
  tenant - and the item is quarantined the same way. No replayed job and no lineage relationship is
  created.
- this is deliberately loud rather than the product-safe not-found used on caller-facing routes.
  There is no caller to disclose to on a background path, and a silent skip would leave the item
  looking merely slow.
- signal: `lotus_report_operations_total{operation="batch_worker_run",status="failed",
  failure_category="batch_item_tenant_mismatch"}` increments on the execution path, and both paths
  log `batch_item_tenant_mismatch` with the batch, item, and report-job identifiers under
  `extra_fields` so they survive the JSON formatter. The replay path additionally logs
  `command=batch_item_replay`.
- **a quarantined item needs a human.** Retry (`:retry-failed`) will not resurrect it, and it is
  excluded from runnable-batch scans, both by design. Establish which of the two tenants the work
  legitimately belongs to before doing anything; the item and the job disagree, so one of them is
  wrong and the fix depends on which.
- expected volume is zero. A mismatch means a link between item and report job was created by a
  worker that was not tenant-scoped, which is only possible for links created before that scoping
  existed. A non-zero count is worth investigating rather than clearing.

Quarantined batch items (`batch_item_report_job_missing`):

- the same check quarantines an item whose linked report job **does not exist**. Only an absent
  row qualifies: a connection or query fault against the report ledger is recorded as an ordinary
  retryable `batch_execution_failed`, never as a quarantine. Quarantine is permanent, so reading a
  brief ledger outage as a dangling link would terminally fail every waiting item at once - a wider
  outage than the stall the check prevents.
  `report_batch_item.report_job_id` carries no foreign key - report jobs live in a separate ledger,
  so one is not expressible - and the lookup runs before the execution error handler. Left to raise,
  a single broken link would abort the whole worker pass and abort it again on the same row every
  interval, so **one unresolvable item would stop every tenant's batches advancing**.
- treated as terminal for the same reason as the tenant mismatch: a dangling link is a durable data
  defect, not a transient failure. Routing it to the retry path would mark it `retryable=True` and
  reproduce the same stall more slowly.
- to diagnose, take the `report_job_id` from the log line and look it up through the report-job
  status surface. It will not be there. Either the job was removed while the batch item still
  referenced it, or the item was written with an identifier that never existed; the two need
  different corrections.
- like the tenant mismatch, this needs a human. It is not cleared by retry and not picked up by a
  later pass.

Still not supported:

- multi-tenant throughput from a single worker process (one process per governed tenant today)
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
    participant WORKER as lotus-report-job-worker
    participant PG as lotus-report-postgres
    participant RENDER as lotus-render
    participant ARCHIVE as lotus-archive

    WB->>GW: POST /api/v1/reports/portfolio-reviews
    GW->>REPORT: POST /reports/portfolio-reviews
    REPORT->>PG: atomically insert request, accepted job, event, work item
    REPORT-->>GW: 202 job handle
    GW-->>WB: 202 product-safe job handle
    WORKER->>PG: claim bounded work-item lease
    WORKER->>PG: complete work item or schedule bounded retry
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
   recent rerender attempt history, render metadata, archive handoff identifiers, and evidence
   links without raw payloads,
5. `POST /reports/jobs/{job_id}/rerender` is only for archived PDF jobs and creates a new
   rerender attempt from the existing immutable snapshot without recollecting upstream data; the
   latest attempts remain discoverable later from the diagnostics view,
6. `POST /reports/jobs/{job_id}/regenerate` is only for archived PDF jobs and creates a new report
   job, fresh upstream snapshot and lineage bundle, and replacement archive document when source
   data must be refreshed,
7. `POST /reports/jobs/{job_id}/replay` is only for failed retry-eligible report jobs and creates
   or reuses a replay-scoped report job without duplicating completed archived documents,
8. `POST /reports/batches/{batch_id}/items/{batch_item_id}/replay` is only for failed
   retry-eligible implementation-backed batch items linked to failed report jobs; it relinks the
   item to replay work and does not change scheduler configuration,
9. cancellation is bounded to pre-render/pre-archive/pre-completion jobs,
10. every accepted report job has one durable `report_request`, one durable `report_job`, one
    `report_job_work_item`, and append-only `report_status_event` rows with a versioned support-safe
    payload contract,
11. a `202` response proves durable acceptance, not capture, render, archive, or delivery completion;
    product callers must poll the source-owned status URL,
12. regenerate, job replay, and batch-item replay persist durable `report_job_relationship` rows
   so operators can navigate source-to-derived and derived-to-source relationships from
   `GET /reports/jobs/{job_id}/diagnostics`.

Use rerender for presentation, template, or rendering corrections where the source snapshot remains
authoritative. Use regenerate when upstream domain data was corrected, late, or incomplete and the
operator needs a replacement document backed by a new lineage bundle. Use failed-work replay only
when the source job or implementation-backed batch item failed before producing a completed archive
document. These commands require `Idempotency-Key` and caller context headers, and they do not
expose raw snapshot payloads, storage keys, or upstream response bodies. New lifecycle events expose
`event_schema_version`, `event_family`, typed `event_payload_json`, and optional
`event_idempotency_key`; legacy rows remain readable as
`report-status-event.legacy.v0` with `payload_posture=legacy_message_only`. New
source/derived relationships expose bounded status, failure category, archive consequence,
archive document ids, actor, and reason; rerender attempt diagnostics expose bounded correction
render/archive state and failed retry posture. These read models do not expose raw snapshot
payloads, storage keys, command idempotency keys, tenant/client/portfolio labels, correlation ids,
trace ids, or database internals.

## RFC-0101 snapshot and lineage flow

RFC-0101 adds durable evidence capture on top of the RFC-0100 job ledger. The first wave is still
owned by `lotus-report`; gateway remains an ingress and status boundary, not the durable evidence
owner.

```mermaid
sequenceDiagram
    participant GW as lotus-gateway or operator caller
    participant REPORT as lotus-report
    participant WORKER as lotus-report-job-worker
    participant CORE as lotus-core
    participant PERF as lotus-performance
    participant RISK as lotus-risk
    participant RENDER as lotus-render
    participant ARCHIVE as lotus-archive
    participant PG as lotus-report-postgres

    GW->>REPORT: POST /reports/portfolio-reviews
    REPORT->>PG: atomically insert request, accepted job, event, work item
    REPORT-->>GW: 202 accepted job handle
    WORKER->>PG: claim work-item lease
    WORKER->>PG: mark job collecting_data
    WORKER->>CORE: summary/detail/allocation/positions/transactions calls
    WORKER->>PERF: workspace summary and contribution calls
    WORKER->>RISK: risk calculation calls
    WORKER->>PG: atomically insert report_input_snapshot and upstream calls
    WORKER->>PG: mark job data_ready or failed
    WORKER->>PG: mark job rendering
    WORKER->>RENDER: POST /renders
    RENDER-->>WORKER: rendered artifact metadata or failure
    WORKER->>PG: mark job completed or failed
    WORKER->>PG: mark job archiving
    WORKER->>ARCHIVE: POST /documents
    ARCHIVE-->>WORKER: archive document id or failure
    WORKER->>PG: mark job archived or failed; complete or retry work item
```

Operational truths for this wave:

1. one report job has zero or one durable snapshot,
2. one snapshot can have many upstream-call rows,
3. snapshot rows are immutable from the application contract perspective,
4. upstream-call rows are append-only from the application contract perspective,
5. snapshot and upstream-call rows commit as one capture transaction,
6. `data_ready` requires a positive declared call count equal to stored rows, stored services covered
   by the declared source-service set, and matching correlation/trace identity,
7. a restart can recollect and restore a same-payload snapshot with zero call rows, but partial or
   conflicting lineage fails closed as `data_incomplete`,
8. a stored failed capture resumes as failed and is never promoted to `data_ready`,
9. support-safe APIs return hashes, posture, and lineage metadata instead of raw upstream payloads.

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
       event.event_type, event.event_schema_version, event.event_family,
       event.event_payload_json, event.event_idempotency_key,
       event.actor, event.correlation_id, event.trace_id, event.created_at
FROM report_status_event event
JOIN report_job job ON job.report_job_id = event.report_job_id
JOIN report_request req ON req.report_request_id = job.report_request_id
WHERE req.idempotency_key = '<idempotency-key>'
ORDER BY event.created_at;

-- replay/regenerate lineage from typed payloads; do not parse message text
SELECT event.report_job_id, event.event_type,
       event.event_payload_json ->> 'replayed_job_id' AS replayed_job_id,
       event.event_payload_json ->> 'regenerated_job_id' AS regenerated_job_id,
       event.event_payload_json ->> 'source_job_id' AS batch_replay_source_job_id
FROM report_status_event event
WHERE event.event_family IN ('replay_lifecycle', 'regenerate_lifecycle', 'batch_item_replay')
ORDER BY event.created_at DESC;

-- durable source/derived relationships for regenerate, replay, and batch item replay
SELECT relationship.relationship_id, relationship.relationship_type,
       relationship.source_report_job_id, relationship.derived_report_job_id,
       relationship.source_status, relationship.derived_status,
       relationship.source_failure_category, relationship.derived_failure_category,
       relationship.archive_consequence, relationship.previous_archive_document_id,
       relationship.new_archive_document_id, relationship.actor, relationship.reason,
       relationship.created_at, relationship.updated_at
FROM report_job_relationship relationship
WHERE relationship.source_report_job_id = '<report-job-id>'
   OR relationship.derived_report_job_id = '<report-job-id>'
ORDER BY relationship.created_at;

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

-- snapshot/call completeness check for one job
SELECT snapshot.report_job_id,
       snapshot.snapshot_id,
       snapshot.lineage_summary_json ->> 'call_count' AS declared_call_count,
       COUNT(call.upstream_call_id) AS stored_call_count,
       ARRAY_AGG(DISTINCT call.service_name) FILTER (WHERE call.service_name IS NOT NULL)
           AS stored_source_services
FROM report_input_snapshot snapshot
LEFT JOIN report_upstream_call call ON call.snapshot_id = snapshot.snapshot_id
WHERE snapshot.report_job_id = '<report-job-id>'
GROUP BY snapshot.report_job_id, snapshot.snapshot_id, snapshot.lineage_summary_json;
```

If declared and stored counts differ, do not manually insert or delete lineage rows and do not move
the job to `data_ready`. Retry through the worker only when the stored call count is zero and the
immutable source payload can be recollected. A non-zero mismatch is a `data_incomplete` integrity
failure that requires source/job evidence review through the existing diagnostics and lineage APIs.

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

The expected gateway response is an immediate `202 Accepted` job handle with `report_request_id`,
`report_job_id`, `status`, `status_url`, and `idempotency_key`. Acceptance means the request, job,
event, and work item are durable; it does not mean source capture, PDF render, or archive handoff
completed. The dedicated report-job worker advances the job asynchronously. Use
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
