# API Surface

Current scope: implementation-backed `lotus-report` API families, product-facing gateway
boundaries, and copy-paste request examples for direct service and support workflows.

## Route Families

| Family | Use | Caller posture |
| --- | --- | --- |
| Integration and aggregations | capability publication, report-ordering catalogue, and portfolio aggregation probes | direct local debugging or gateway discovery |
| Report jobs | durable portfolio-review report initiation, status, evidence, and correction commands | governed caller context; idempotency on duplicate-safe mutations |
| Batch reporting | internal batch materialization, status, control, item replay, and bounded run-once operations | governed caller context; idempotency on creation and item replay |

## Integration

- `GET /integration/capabilities`
  reporting capability publication for downstream consumers
- `GET /integration/report-ordering-catalogue`
  versioned Report-owned business catalogue for supported report families, ordering modes,
  formats, configuration fields, sections, release posture, and live Render supportability. See
  [Report Ordering](Report-Ordering).
- `GET /integration/report-ordering-catalogue/advisor-commentary-availability`
  pre-order availability of the ADVISOR_COMMENTARY section for one portfolio and report
  context (`portfolio_id` + `X-Tenant-Id`, optional `as_of_date`/`reporting_currency`):
  `ready` carries the accepted brief run id the order must supply as
  `options.advisor_brief_run_id`; `unavailable` carries one bounded reason -
  `advisor_brief_not_reviewed`, `advisor_brief_context_mismatch`, or
  `advisor_brief_availability_unknown` (the lotus-ai lookup could not answer - deliberately
  distinct from not_reviewed, because a failed lookup proves nothing). Backed by the
  lotus-ai latest-accepted lookup (lotus-ai#183); see
  [Portfolio Review Report](Portfolio-Review-Report).

## Aggregations

- `GET /aggregations/portfolios/{portfolio_id}`
  aggregated portfolio rows by as-of date

## Reports

- `POST /reports/portfolios/{portfolio_id}/summary`
  lotus-report-owned portfolio summary payload
- `POST /reports/portfolios/{portfolio_id}/review`
  machine-readable portfolio review report payload for client/advisor meetings
- `POST /reports/portfolio-reviews`
  internal durable portfolio review report acceptance; atomically persists the request, accepted
  job, lifecycle event, and work item before returning a job handle. Source capture, render, and
  archive continue in the dedicated report job worker. It can carry an optional
  `proposal_narrative_package` from `lotus-advise` when that package is already approved for advisor
  use. `202 Accepted` does not mean the report is complete; poll the status URL.
- `POST /reports/idea-evidence-packs`
  implemented, not-certified source-safe intake route for reviewed `lotus-idea` evidence packs.
  The route requires `Idempotency-Key`, persists support-safe intake fingerprints and caller
  context in the `IDEA_EVIDENCE_INTAKE_LEDGER_PATH` SQLite ledger, replays same-payload retries
  across process restarts, and rejects changed-payload replays. It does not create report jobs,
  render output, archive records, or client-publication authority. Report validates the retention
  policy reference and tenant scope before writing the intake ledger.
- `POST /reports/idea-evidence-packs/materializations`
  implemented, not-certified report-owned materialization route for reviewed `lotus-idea`
  evidence packs. The route requires governed caller context and `Idempotency-Key`, validates
  Report-owned retention authority and tenant scope, creates or replays the governed proof-pack
  report job, captures immutable lineage to `lotus-idea`, drives existing render/archive lifecycle
  wiring for PDF output, and returns a typed source-safe receipt with report-package identity,
  source authority, render/archive outcome flags and identifiers, evidence refs, and remaining
  blockers. It does not grant client-publication authority, suitability, mandate approval,
  execution, distribution, or supported-feature promotion.
- `GET /reports/idea-evidence-packs/materializations`
  read-only recovery for a Report commit whose POST response was lost. The caller supplies the
  original idempotency key and exact evidence-pack, conversion-intent, candidate, evidence-packet,
  evidence-fingerprint, and portfolio identities. Report checks the admitted `lotus-idea`
  application and `report.idea-materialization.recover` capability before repository access,
  performs a bounded tenant-scoped lookup, and returns the current canonical receipt only when the
  persisted identity and report request agree. Not-found is `404`; drift, ambiguity or malformed
  stored identity is `409`. The GET never starts, retries, renders or archives work.
- `POST /reports/outcome-reviews`
  internal durable post-trade outcome-review report job initiation from manage-owned
  `DpmOutcomeReportInput`; persists the handoff as the immutable snapshot, records lineage to
  `lotus-manage`, requires source hashes/evidence refs/redaction/retention/supportability posture
  before durable capture, carries optional `portfolio_memory_context` as bounded lineage only
  including context hash and event-ref selection/truncation posture when supplied, and uses the
  governed render/archive lifecycle for PDF artifacts
- `POST /reports/proof-packs`
  internal durable pre-trade proof-pack report job initiation from manage-owned
  `DpmProofPackReportInput`; persists the handoff as the immutable snapshot, records lineage to
  `lotus-manage`, requires source hashes/evidence refs/redaction/retention/supportability posture
  before durable capture, carries optional `portfolio_memory_context` as bounded lineage only
  including context hash and event-ref selection/truncation posture when supplied, builds a
  `proof_pack` render package for `lotus-render`, and uses the governed render/archive lifecycle
  for PDF artifacts
- `POST /reports/rebalance-waves`
  internal durable rebalance-wave report job initiation from manage-owned `DpmWaveReportInput`;
  persists the handoff as the immutable snapshot, records lineage to `lotus-manage`, requires
  source hashes/evidence refs/redaction/retention/supportability posture before durable capture,
  carries optional `portfolio_memory_context` as bounded lineage only including context hash and
  event-ref selection/truncation posture when supplied, builds a `rebalance_wave` render package
  for `lotus-render`, and uses the governed render/archive lifecycle for PDF artifacts
  without recomputing wave state, proof-pack linkage, internal handoff evidence, or external
  execution posture
- `GET /reports/jobs` — scoped to the ADMITTED caller tenant and region (X-Tenant-Id/X-Region); a contradicting tenantId/region filter is refused 400 (tenant_filter_conflicts_with_caller / region_filter_conflicts_with_caller)
  internal operator-safe bounded search for report jobs by tenant, region, status, report type,
  portfolio id, as-of date, idempotency key, correlation id, and created-at window
- `GET /reports/jobs/{job_id}`
  internal product-safe report job status and diagnostics
- `GET /reports/jobs/{job_id}/diagnostics`
  internal RFC-0105 operator diagnostics view composed from source-backed job, event, snapshot,
  lineage, durable regenerate/replay source-derived relationships, recent rerender attempt
  history, render, and archive handoff state; omits raw payloads, storage references, command
  idempotency keys, correlation ids, trace ids, and database internals. The snapshot block
  separates two facts that must never be conflated: `reproduction_availability` states what the
  SNAPSHOT holds (`snapshot_recomposition` for a successful capture, `none` for failure
  evidence; rows stamped `rerender_from_snapshot` under lifecycle policy 1.0.0 read as
  `snapshot_recomposition` without the stored row changing), and `rerender_available` states
  whether the executable rerender COMMAND is available right now - derived from the same
  predicate that gates `POST /reports/jobs/{job_id}/rerender` (archived PDF job with render and
  archive identities), so JSON-only, failed, and unfinished-PDF jobs truthfully advertise no
  rerender path even while the snapshot capability stands
- `GET /reports/jobs/{job_id}/portfolio-memory-events`
  internal report-owned source-event family for downstream portfolio memory; maps report
  lifecycle, snapshot, render, and archive evidence into stable event identities, source refs,
  artifact refs, hashes, and retention/redaction/access/audit policy without exposing raw snapshot
  payloads or storage references. An event's identity is fixed at event time (preimage version
  `eip2`: job, transition, portfolio, timestamp) and never moves afterwards - not as the job
  captures a snapshot, renders or archives, and not across a redeployment, so a consumer may
  deduplicate on `event_identity` safely. Facts that arrive later (snapshot, artifact, archive
  document, report revision) appear as refs outside that preimage, each only on events at or
  after the step that produced it. Identities emitted before `eip2` were computed at read time
  and never stored: they are not reconstructible and none is claimed - re-key once by the stable
  `event_id`, which is unchanged across both versions
- `GET /reports/jobs/{job_id}/events`
  internal append-only report job lifecycle event history with versioned support-safe typed
  payloads; legacy rows remain readable as legacy message-only events, and replay/regenerate
  lineage consumers must not parse human-readable event messages
- `POST /reports/jobs/{job_id}/rerender`
  internal RFC-0105 rerender command for already archived PDF jobs; reuses the immutable snapshot,
  preserves snapshot id/hash, creates a new render/archive correction identity, and does not
  recollect upstream data; successful and failed rerender attempts are rediscoverable from
  diagnostics after the initial command response is unavailable
- `POST /reports/jobs/{job_id}/regenerate`
  internal RFC-0105 regenerate command for already archived PDF jobs; recollects upstream data into
  a fresh snapshot and lineage bundle, creates a replacement archive document, persists a
  source-derived relationship, and returns explicit old/new job, snapshot, hash, and archive
  document identities
- `POST /reports/jobs/{job_id}/replay`
  internal RFC-0105 failed-work replay command for failed retry-eligible report jobs; creates or
  reuses a replay-scoped report job, persists a source-derived relationship, and rejects completed,
  archived, cancelled, or non-retryable source jobs
- `GET /reports/jobs/{job_id}/snapshot`
  internal durable report input snapshot lookup by job id
- `GET /reports/jobs/{job_id}/lineage`
  internal durable upstream-call lineage lookup by job id
- `POST /reports/jobs/{job_id}/cancel`
  internal bounded cancellation before render, archive, or completion phases
- `GET /reports/snapshots/{snapshot_id}`
  internal durable report input snapshot lookup by snapshot id
- `GET /reports/snapshots/{snapshot_id}/lineage`
  internal durable upstream-call lineage lookup by snapshot id
- `POST /reports/batches`
  internal durable batch materialization from a governed portfolio selector; returns a batch handle
  and status URL
- `GET /reports/batches/{batch_id}`
  internal product-safe batch status, item summary, status counts, linked report-job lifecycle,
  and source-owned archive document identity when the exact linked job is archived
- `GET /reports/batches/{batch_id}/items/{batch_item_id}`
  internal item status with the durable `report_job_id`, source `report_job_status`, and optional
  `archive_document_id`; the document id remains null before archive completion or when the linked
  job is unavailable
- `POST /reports/batches/{batch_id}:pause`
  internal batch pause control before pending items are leased
- `POST /reports/batches/{batch_id}:resume`
  internal batch resume control for paused batches
- `POST /reports/batches/{batch_id}:cancel`
  internal batch cancellation before pending items are leased
- `POST /reports/batches/{batch_id}:retry-failed`
  internal bounded retry control for failed batch items
- `POST /reports/batches/{batch_id}:recover-expired-leases`
  internal expired-lease recovery control for batch items
- `POST /reports/batches/{batch_id}:run-once`
  internal bounded operator-controlled batch run over recovery, dispatch, report-job execution, and
  batch-item reconciliation for one explicit batch
- `POST /reports/batches/{batch_id}/items/{batch_item_id}/replay`
  internal RFC-0105 failed-work replay command for implementation-backed RFC-0104 batch items whose
  linked report job failed; relinks the item to a replay-scoped report job without scheduler CRUD,
  registry mutation, or archive distribution behavior
- `GET /reports/batch-schedules`
  internal list of governed report batch schedules: configured schedules plus the caller
  tenant's stored recurring definitions with a `next_run_at` projection
- `POST /reports/batch-schedules`
  creates a durable, tenant-fenced recurring report-pack schedule (explicit portfolio list,
  `monthly_end` or `quarter_end` cadence) validated through the governed report-ordering
  catalogue; an identical retry converges on the already-created schedule
- `GET /reports/batch-schedules/{schedule_id}`
  one stored schedule with its full governance audit trail; foreign or unknown ids return the
  same not-found shape, so schedule ids are not an existence oracle across tenants
- `PATCH /reports/batch-schedules/{schedule_id}`
  partial update, enable, or disable; disabling stops future runs without deleting the
  definition or any batch history; every effective change is audited with a field-level diff
- `POST /reports/batch-schedules:run-due`
  internal bounded scheduler pass that materialises batches for schedules currently due -
  configured schedules and due stored definitions of the scheduler's own tenant ride the same
  loop, so stored-schedule batches carry `batch_schedule_id` lineage exactly like configured
  ones; an optional `evaluation_date` lets an operator simulate a period-end pass

  Unlike every other route on this page, this one does **not** derive its tenant from the calling
  caller context. `run_due_report_batch_schedules` builds its context from
  `batch_scheduler_caller_context(config, ...)`, so the tenant comes from the scheduler's own
  configuration rather than from the `X-Caller-App` identity that invoked it. It materialises new
  batches and performs no lookup of existing tenant-scoped state, so it is a creation path rather
  than a cross-tenant read — but a caller cannot select the tenant a scheduler pass acts for, and
  that assumption is tracked as [#177](https://github.com/sgajbi/lotus-report/issues/177).
- `GET /reports/operations/attention`
  internal RFC-0105 source-backed attention scan for active report jobs and batch items; returns
  bounded stuck-state and SLA-breach events with opaque identifiers, thresholds, age, bounded
  reasons, and evidence links, without raw payloads, tenant, portfolio, correlation, or trace
  identifiers

## Product-facing boundary

Front-office callers must use `lotus-gateway` for report job initiation and status:

- `POST /api/v1/reports/portfolio-reviews`
- `GET /api/v1/report-jobs`
- `GET /api/v1/report-jobs/{job_id}`
- `GET /api/v1/report-jobs/{job_id}/events`
- `POST /api/v1/report-jobs/{job_id}/cancel`

`lotus-report` owns the durable ledger and internal orchestration state. `lotus-gateway` owns the
product-facing ingress, caller context enforcement, and response posture for Workbench and other
front-office consumers.

The report-ordering catalogue is currently a Report integration contract. Gateway issue
[`#499`](https://github.com/sgajbi/lotus-gateway/issues/499) owns entitlement and selected-scope
projection before Workbench consumption. Workbench must not call Report directly or hard-code
report choices while that Gateway contract remains unmerged.

## Platform surfaces

- `/health`
- `/health/live`
- `/health/ready`
- `/metrics`
- `/docs`

## Current contract notes

- integration capability query parameters are canonical snake_case: `consumer_system`, `tenant_id`
- aggregation query parameter is canonical snake_case: `as_of_date`
- report summary/review query parameters use canonical `section_limit`
- portfolio review request bodies use canonical snake_case fields only
- report job creation requires `Idempotency-Key`
- optional portfolio-review `proposal_narrative_package` input is accepted only when
  `package_status` is `INCLUDED_REVIEWED_NARRATIVE`, `review.review_state` is
  `APPROVED_FOR_ADVISOR_USE`, and `source_lineage.source_narrative_hash` is present. The package
  is preserved in the immutable snapshot and render package; `lotus-report` does not approve,
  rewrite, or infer advisory narrative content.
- rerender, regenerate, and failed-work replay commands require `Idempotency-Key` plus governed
  caller context headers
- report-owned portfolio-memory events require governed caller context headers and return
  support-safe event identities only; they do not expose raw report inputs, raw upstream payloads,
  rendered bytes, storage keys, or client communication content
- report job search requires at least one supported filter and is bounded by `limit`
- idea-evidence materialization requires `Idempotency-Key` and governed caller context headers;
  same-key/same-payload retries replay the report-package receipt, while changed-payload replay is
  rejected before any publication or execution authority can be inferred
- batch materialization requires `Idempotency-Key` and governed caller context headers
- batch materialization, control, run-once, and config-backed scheduler list/run-due APIs are
  internal `lotus-report` APIs; the bounded runtime pass and `lotus-report-batch-worker` process
  are internal service primitives, not APIs; schedule CRUD and entitlement-certified public
  scheduler runtime remain future scope
- PDF-capable report jobs submit a governed render package to `lotus-render`; after successful
  render completion they hand the artifact and source-backed metadata to `lotus-archive`
- successful job initiation captures a durable snapshot and upstream lineage before the job reaches
  `data_ready`, and PDF jobs may then advance through `rendering`, `completed`, `archiving`, and
  `archived`
- archive retrieval, retention execution, legal hold, purge, and document distribution remain owned
  by `lotus-archive`
- snapshot and lineage endpoints are support-safe evidence APIs; they return hashes, posture, and
  summary metadata instead of raw upstream payload internals

## Request examples

Create this reusable caller-context config before running operator or gateway examples that require
governed caller context:

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

Use a stable operation-specific `Idempotency-Key` only on mutations that require duplicate-safe
replay or conflict detection: report-job creation, rerender, regenerate, failed-work replay, batch
materialization, and batch-item replay. Reuse the same idempotency key only for an intentional retry
of the same canonical request.

Integration capabilities:

```bash
curl "http://127.0.0.1:8300/integration/capabilities?consumer_system=lotus-gateway&tenant_id=default"
```

Report ordering catalogue:

```bash
curl "http://127.0.0.1:8300/integration/report-ordering-catalogue" \
  --config report-operator-headers.curl
```

Aggregations:

```bash
curl "http://127.0.0.1:8300/aggregations/portfolios/DEMO_DPM_EUR_001?as_of_date=2026-02-24&live=false"
```

Portfolio summary:

```bash
curl -X POST "http://127.0.0.1:8300/reports/portfolios/DEMO_DPM_EUR_001/summary?section_limit=10" \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: local-doc-probe" \
  -d "{\"as_of_date\":\"2026-02-24\",\"reporting_currency\":\"EUR\"}"
```

Portfolio review:

```bash
curl -X POST "http://127.0.0.1:8300/reports/portfolios/DEMO_DPM_EUR_001/review?section_limit=10" \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: local-doc-probe" \
  -d "{\"as_of_date\":\"2026-02-24\",\"reporting_currency\":\"EUR\",\"benchmark_code\":\"MSCI_ACWI\"}"
```

Canonical front-office portfolio review:

```bash
curl -X POST "http://127.0.0.1:8300/reports/portfolios/PB_SG_GLOBAL_BAL_001/review?section_limit=20" \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: portfolio-review-local-proof" \
  -d "{\"as_of_date\":\"2026-04-22\",\"reporting_currency\":\"USD\",\"benchmark_code\":\"BMK_PB_GLOBAL_BALANCED_60_40\",\"sections\":[\"CLIENT_PROFILE\",\"OVERVIEW\",\"ALLOCATION\",\"PERFORMANCE\",\"RISK_ANALYTICS\",\"INCOME_AND_ACTIVITY\",\"HOLDINGS\",\"TRANSACTIONS\"]}"
```

Portfolio review report job:

```bash
curl -X POST "http://gateway.dev.lotus:8111/api/v1/reports/portfolio-reviews" \
  --config report-operator-headers.curl \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22" \
  -d "{\"portfolio_scope\":{\"portfolio_ids\":[\"PB_SG_GLOBAL_BAL_001\"]},\"as_of_date\":\"2026-04-22\",\"requested_output_formats\":[\"pdf\"],\"reporting_currency\":\"USD\",\"options\":{\"sections\":[\"OVERVIEW\",\"PERFORMANCE\",\"RISK_ANALYTICS\"],\"benchmark_code\":\"BMK_PB_GLOBAL_BALANCED_60_40\"},\"proposal_narrative_package\":{\"package_status\":\"INCLUDED_REVIEWED_NARRATIVE\",\"usage\":\"REPORT_REQUEST_APPROVED_ADVISOR_NARRATIVE\",\"proposal_id\":\"prop_001\",\"proposal_version_no\":3,\"narrative_id\":\"pnar_001\",\"review\":{\"review_id\":\"pnrev_001\",\"review_state\":\"APPROVED_FOR_ADVISOR_USE\"},\"source_lineage\":{\"source_narrative_hash\":\"sha256:narrative\"},\"sections\":[{\"section_id\":\"portfolio_context\",\"title\":\"Portfolio Context\",\"body\":\"The portfolio remains aligned to the balanced mandate.\"}],\"disclosures\":[{\"disclosure_id\":\"proposal_narrative.advisor_use_only.v1\",\"text\":\"For advisor use only until client-ready approval is complete.\"}]}}"
```

Report job status:

```bash
curl "http://gateway.dev.lotus:8111/api/v1/report-jobs/rjob_example" \
  --config report-operator-headers.curl
```

Internal report snapshot lookup:

```bash
curl "http://127.0.0.1:8300/reports/jobs/rjob_example/snapshot" \
  --config report-operator-headers.curl
```

Internal report lineage lookup:

```bash
curl "http://127.0.0.1:8300/reports/jobs/rjob_example/lineage" \
  --config report-operator-headers.curl
```

Internal report-owned portfolio-memory source events:

```bash
curl "http://127.0.0.1:8300/reports/jobs/rjob_example/portfolio-memory-events" \
  --config report-operator-headers.curl
```

Report job operational search:

```bash
curl "http://gateway.dev.lotus:8111/api/v1/report-jobs?tenantId=tenant-sg&region=APAC&portfolioId=PB_SG_GLOBAL_BAL_001&status=archived&limit=25" \
  --config report-operator-headers.curl
```

For direct `lotus-report` proof after RFC-0101, the equivalent support-safe evidence paths are:

- `GET /reports/jobs/{job_id}/snapshot`
- `GET /reports/jobs/{job_id}/lineage`
- `GET /reports/snapshots/{snapshot_id}`
- `GET /reports/snapshots/{snapshot_id}/lineage`

Report job cancellation:

```bash
curl -X POST "http://gateway.dev.lotus:8111/api/v1/report-jobs/rjob_example/cancel" \
  --config report-operator-headers.curl
```

Report job rerender, regenerate, and failed-work replay:

```bash
curl -X POST "http://127.0.0.1:8300/reports/jobs/rjob_example/rerender" \
  --config report-operator-headers.curl \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: rerender-rjob_example-v1" \
  -d "{\"reason\":\"correct_template_or_presentation\"}"

curl -X POST "http://127.0.0.1:8300/reports/jobs/rjob_example/regenerate" \
  --config report-operator-headers.curl \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: regenerate-rjob_example-v1" \
  -d "{\"reason\":\"refresh_corrected_upstream_data\"}"

curl -X POST "http://127.0.0.1:8300/reports/jobs/rjob_failed_example/replay" \
  --config report-operator-headers.curl \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: replay-rjob_failed_example-v1" \
  -d "{\"reason\":\"retry_failed_work\"}"
```

Internal batch materialization:

```bash
curl -X POST "http://127.0.0.1:8300/reports/batches" \
  --config report-operator-headers.curl \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: batch-PB_SG_GLOBAL_BAL_001-2026-04-22" \
  -d "{\"selector_mode\":\"explicit_portfolio_list\",\"portfolio_ids\":[\"PB_SG_GLOBAL_BAL_001\"],\"source_candidates\":[{\"portfolio_id\":\"PB_SG_GLOBAL_BAL_001\",\"tenant_id\":\"tenant-sg\",\"region\":\"APAC\",\"active\":true,\"selected\":true,\"source_system\":\"lotus-core\",\"source_object\":\"PortfolioScope\"}],\"as_of_date\":\"2026-04-22\",\"requested_output_formats\":[\"pdf\"],\"reporting_currency\":\"USD\"}"
```

Internal batch status:

```bash
curl "http://127.0.0.1:8300/reports/batches/rbatch_example" \
  --config report-operator-headers.curl
```

Batch and batch-item status reads are scoped to the tenant in the required caller context. An
unknown batch and a batch owned by another tenant both return the same product-safe
`report_batch_not_found` response before Report looks up linked job or archive status. This is an
internal defense-in-depth boundary; it does not claim production identity or entitlement
certification.

Internal batch control:

```bash
curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example:pause" \
  --config report-operator-headers.curl
```

The other certified controls use the same governed caller-context headers:

```bash
curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example:resume" \
  --config report-operator-headers.curl

curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example:cancel" \
  --config report-operator-headers.curl

curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example:retry-failed" \
  --config report-operator-headers.curl

curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example:recover-expired-leases" \
  --config report-operator-headers.curl
```

Internal batch item replay:

```bash
curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example/items/rbci_failed_example/replay" \
  --config report-operator-headers.curl \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: batch-item-replay-rbci_failed_example-v1" \
  -d "{\"reason\":\"retry_failed_batch_item\"}"
```

Internal bounded batch run:

```bash
curl -X POST "http://127.0.0.1:8300/reports/batches/rbatch_example:run-once" \
  --config report-operator-headers.curl \
  -H "Content-Type: application/json" \
  -d "{\"worker_id\":\"lotus-report-batch-worker-1\",\"recover_expired_leases\":true,\"dispatch_policy\":{\"max_active_batches\":1,\"max_active_items\":5,\"max_active_upstream_jobs\":3,\"max_active_render_jobs\":2,\"max_active_archive_jobs\":2,\"lease_seconds\":300},\"runtime_load\":{\"active_batches\":0,\"active_items\":0,\"active_upstream_jobs\":0,\"active_render_jobs\":0,\"active_archive_jobs\":0}}"
```

Current batch APIs are direct `lotus-report` internal APIs. Gateway routes, Workbench batch
surfaces, scheduled execution, and long-running runtime telemetry remain future scope.

The review response is a typed report contract. It separates client-ready `client_sections` from
advisor-only `advisor_sections`, carries explicit section readiness states including
`not_applicable` for requested supporting sections with no applicable activity, includes
report-level `evidence`, exposes source-backed client/mandate profile context from lotus-core,
position cost/unrealized P&L, transaction-level realized P&L, and YTD contribution where upstream
services provide them. It also
includes deterministic `report_structure`, `advisor_briefing`, guarded `ai_readiness`, and
`upstream_capability_audit` metadata so front-office consumers can organize a review meeting without
treating report gaps as advice or silently losing upstream dependency gaps.
Portfolio review capability keys are published through `GET /integration/capabilities`.
Report job capability keys and PDF render-submission posture are also published there once implementation-backed.

Detailed response-family guidance lives in [Portfolio Review Report](Portfolio-Review-Report).

Use these examples as the canonical public API shape. Swagger must not publish stale placeholder
reporting endpoints, RFC names, or duplicate camelCase aliases.
