# Durability and Consistency Standard (lotus-report)

- Standard reference: `lotus-platform/Durability and Consistency Standard.md`
- Scope: reporting and aggregation read APIs built from lotus-core/lotus-performance sourced data.
- Change control: RFC required for standard changes; ADR required for temporary deviations.

## Workflow Consistency Classification

- Strong consistency:
  - deterministic report payload generation for same request and `as_of_date`
  - reproducibility metadata in response contracts
- Eventual consistency:
  - upstream lotus-core/lotus-performance source freshness prior to request execution

## Idempotency and Write Semantics

- Report-job, batch, replay, rerender, and materialization commands require governed idempotency
  identities at their public or internal command boundaries.
- Report input snapshots remain one-to-one with a report job and immutable by payload hash.
- Upstream-call evidence is append-only. A same-job retry may restore a completely missing call
  ledger only when the snapshot payload hash is unchanged; partial or conflicting lineage fails
  closed.
- Evidence:
  - `src/app/reporting_jobs/ledger.py`
  - `src/app/reporting_jobs/postgres_ledger.py`
  - `src/app/reporting_lineage/store.py`
  - `src/app/reporting_lineage/postgres_store.py`

## Atomicity Boundaries

- Report request, accepted job, initial event, and work item are one durable acceptance unit.
- A captured report input snapshot and all upstream-call evidence are one transaction. A call-row
  insertion failure rolls back a newly inserted snapshot.
- A job reaches `data_ready` only after the persisted lineage satisfies the capture invariant:
  declared call count is positive and equals the stored row count, stored call services are
  declared by the snapshot summary, and correlation/trace identity matches the snapshot.
- A restart may re-collect and restore a legacy snapshot with zero stored calls. A partially
  written or conflicting call ledger is not extended speculatively; the job fails with
  `data_incomplete` for operator review.
- Evidence:
  - `src/app/reporting_jobs/service.py`
  - `src/app/reporting_lineage/capture_service.py`
  - `src/app/reporting_lineage/store.py`
  - `src/app/reporting_lineage/postgres_store.py`
  - `tests/unit/reporting_lineage/test_capture_service.py`
  - `tests/integration/test_postgres_report_input_snapshot_store.py`

## As-Of and Reproducibility Semantics

- `as_of_date` is a mandatory request field for reporting workflows.
- Responses include contract/policy versions where applicable.
- Evidence:
  - `src/app/models/contracts.py`
  - `src/app/services/reporting_read_service.py`
  - `tests/unit/test_reporting_read_service_additional.py`

## Concurrency and Conflict Policy

- Read-model processing is deterministic for equivalent source inputs. Durable workflows use
  immutable hashes, idempotency keys, leased work claims, and explicit conflict errors.
- Upstream call retries are bounded and explicit. The shared HTTP helper retries transport
  failures and transient HTTP statuses `429`, `502`, `503`, and `504` within
  `UPSTREAM_MAX_RETRIES`; validation, authorization, not-found, conflict, and business-rule
  statuses pass through immediately.
- `Retry-After` response headers are honored for transient status retries with a bounded maximum
  delay so downstream overload does not create unbounded sleeps or retry storms.
- Evidence:
  - `src/app/clients/http_resilience.py`
  - `tests/unit/test_http_resilience.py`

## Integrity Constraints

- Request schema validation enforces section/as-of contract integrity.
- Invalid request shapes are rejected with explicit 4xx responses.
- Successful capture cannot resume from snapshot presence alone. The worker verifies the complete
  snapshot/lineage unit before `data_ready`; a stored failed capture is replayed as failure and is
  never promoted to readiness.
- Evidence:
  - `src/app/models/*`
  - `tests/integration/test_api.py`

## Release-Gate Tests

- Unit: `tests/unit/*`
- Integration: `tests/integration/*`
- E2E: `tests/e2e/*`
- PostgreSQL transaction/restart proof:
  `tests/integration/test_postgres_report_input_snapshot_store.py`

## Deviations

- Any new multi-write workflow without an explicit transaction boundary, idempotency policy,
  restart invariant, and failure-injection proof requires an ADR with an expiry review date.
