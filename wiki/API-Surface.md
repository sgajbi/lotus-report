# API Surface

Current scope: implementation-backed `lotus-report` API families, product-facing gateway
boundaries, and copy-paste request examples for direct service and support workflows.

## Route Families

| Family | Use | Caller posture |
| --- | --- | --- |
| Integration and aggregations | capability publication and portfolio aggregation probes | direct local debugging or gateway discovery |
| Report jobs | durable portfolio-review report initiation, status, evidence, and correction commands | governed caller context; idempotency on duplicate-safe mutations |
| Batch reporting | internal batch materialization, status, control, item replay, and bounded run-once operations | governed caller context; idempotency on creation and item replay |

## Integration

- `GET /integration/capabilities`
  reporting capability publication for downstream consumers

## Aggregations

- `GET /aggregations/portfolios/{portfolio_id}`
  aggregated portfolio rows by as-of date

## Reports

- `POST /reports/portfolios/{portfolio_id}/summary`
  lotus-report-owned portfolio summary payload
- `POST /reports/portfolios/{portfolio_id}/review`
  machine-readable portfolio review report payload for client/advisor meetings
- `POST /reports/portfolio-reviews`
  internal durable portfolio review report job initiation; returns a job handle and, for PDF jobs,
  may advance through render completion before the response returns. It can carry an optional
  `proposal_narrative_package` from `lotus-advise` when that package is already approved for
  advisor use.
- `POST /reports/outcome-reviews`
  internal durable post-trade outcome-review report job initiation from manage-owned
  `DpmOutcomeReportInput`; persists the handoff as the immutable snapshot, records lineage to
  `lotus-manage`, carries optional `portfolio_memory_context` as bounded lineage only, and uses
  the governed render/archive lifecycle for PDF artifacts
- `POST /reports/proof-packs`
  internal durable pre-trade proof-pack report job initiation from manage-owned
  `DpmProofPackReportInput`; persists the handoff as the immutable snapshot, records lineage to
  `lotus-manage`, carries optional `portfolio_memory_context` as bounded lineage only, builds a
  `proof_pack` render package for `lotus-render`, and uses the governed render/archive lifecycle
  for PDF artifacts
- `POST /reports/rebalance-waves`
  internal durable rebalance-wave report job initiation from manage-owned `DpmWaveReportInput`;
  persists the handoff as the immutable snapshot, records lineage to `lotus-manage`, carries
  optional `portfolio_memory_context` as bounded lineage only, builds a `rebalance_wave` render
  package for `lotus-render`, and uses the governed render/archive lifecycle for PDF artifacts
  without recomputing wave state, proof-pack linkage, internal handoff evidence, or external
  execution posture
- `GET /reports/jobs`
  internal operator-safe bounded search for report jobs by tenant, region, status, report type,
  portfolio id, as-of date, idempotency key, correlation id, and created-at window
- `GET /reports/jobs/{job_id}`
  internal product-safe report job status and diagnostics
- `GET /reports/jobs/{job_id}/diagnostics`
  internal RFC-0105 operator diagnostics view composed from source-backed job, event, snapshot,
  lineage, render, and archive handoff state; omits raw payloads and storage references
- `GET /reports/jobs/{job_id}/portfolio-memory-events`
  internal report-owned source-event family for downstream portfolio memory; maps report
  lifecycle, snapshot, render, and archive evidence into stable event identities, source refs,
  artifact refs, hashes, and retention/redaction/access/audit policy without exposing raw snapshot
  payloads or storage references
- `GET /reports/jobs/{job_id}/events`
  internal append-only report job lifecycle event history with versioned support-safe typed
  payloads; legacy rows remain readable as legacy message-only events, and replay/regenerate
  lineage consumers must not parse human-readable event messages
- `POST /reports/jobs/{job_id}/rerender`
  internal RFC-0105 rerender command for already archived PDF jobs; reuses the immutable snapshot,
  preserves snapshot id/hash, creates a new render/archive correction identity, and does not
  recollect upstream data
- `POST /reports/jobs/{job_id}/regenerate`
  internal RFC-0105 regenerate command for already archived PDF jobs; recollects upstream data into
  a fresh snapshot and lineage bundle, creates a replacement archive document, and returns explicit
  old/new job, snapshot, hash, and archive document identities
- `POST /reports/jobs/{job_id}/replay`
  internal RFC-0105 failed-work replay command for failed retry-eligible report jobs; creates or
  reuses a replay-scoped report job and rejects completed, archived, cancelled, or non-retryable
  source jobs
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
  internal product-safe batch status, item summary, and status counts
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
