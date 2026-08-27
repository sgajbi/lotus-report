# Security and Governance

## Current governance

- RFC-0050
  core, analytics, and reporting service boundaries
- RFC-0067
  centralized OpenAPI and vocabulary governance
- RFC-0071
  environment-scoped service addressing and ingress posture
- RFC-0072
  multi-lane CI and release governance
- RFC-0073
  ecosystem context and agent guidance system

## Repo-specific guardrails

- OpenAPI quality gate is active
- typecheck is part of the fast gate
- migration smoke and security audit are part of PR-grade validation
- time-bounded dependency vulnerability exceptions are governed by
  `docs/standards/dependency-vulnerability-exceptions.json`; `make security-audit` fails when an
  exception is expired, missing ownership, or not linked to a GitHub issue
- direct write requests are bounded by `ENTERPRISE_MAX_WRITE_PAYLOAD_BYTES`; malformed
  `Content-Length`, missing-length oversized bodies, and streamed bodies over the cap are rejected
  before route handling
- enterprise readiness and observability behavior are covered by unit tests
- direct local debugging must use `ENTERPRISE_RUNTIME_PROFILE=local`; production-like profiles
  (`prod`, `production`, `preprod`, `staging`, and `uat`) fail closed by enforcing read and write
  authorization even when authz toggles are omitted
- production-like direct service startup requires `ENTERPRISE_ENFORCE_AUTHZ=true`,
  `ENTERPRISE_ENFORCE_READ_AUTHZ=true`, and `ENTERPRISE_PRIMARY_KEY_ID`; otherwise runtime
  validation raises `enterprise_runtime_config_invalid`
- enterprise read audit events are toggle-backed with `ENTERPRISE_AUDIT_READS`
- portfolio review responses preserve source refs, readiness state, report coverage, and
  advisor/client separation so downstream consumers can distinguish sourced facts from missing
  evidence
- AI readiness is metadata only; the report endpoint does not issue trade recommendations,
  suitability determinations, or inferred client-profile facts

## Tenant admission on report batches

The principal access control in this service, and the one a reader most needs to understand before
touching batch code. It is implemented once, in
`src/app/report_batch_orchestrator/tenant_admission.py`.

**Every externally invocable batch mutation, control, replay or execution path admits the caller
through that module before touching durable state.** The rule lives there rather than in the HTTP
layer because the batch worker and the replay service are driven by background processes that never
pass through a router — putting it in a router dependency would have left those paths unguarded.

Two properties are deliberate and worth relying on:

**Admission is fail-closed.** `admit_batch` compares the persisted batch tenant against the caller
tenant and raises on any mismatch. There is no permissive branch.

**Cross-tenant existence is never disclosed.** A batch owned by another tenant raises exactly the
same `report_batch_not_found` signal as an identifier that does not exist, so the error contract
cannot be used as an existence oracle. The ledger already raises that signal for unknown
identifiers, which is what makes the two indistinguishable rather than merely similar.

Where admission is applied:

| path | where the caller is admitted |
|---|---|
| batch control routes (`:pause`, `:resume`, `:cancel`, `:retry-failed`, `:recover-expired-leases`) | `load_admitted_batch` in the router, before the ledger mutation |
| batch and item status reads | `load_admitted_batch`, then a tenant-scoped archive-status join |
| item replay | `admit_batch` inside the replay service, before the item is loaded |
| `:run-once` | `admit_batch` at the top of the worker's `run_once`, before status checks, lease recovery or dispatch |
| background runtime pass | `tenant_id` is a required keyword on `list_runnable_batch_ids`, applied as a SQL predicate |
| execution bridge | the item's linked report job is re-checked against the batch tenant before any render or archive work |

That last row matters and is easy to miss: an item-to-report-job link written before dispatch was
tenant-scoped can point at another tenant's job even when the batch itself is correctly owned.
Status projection, replay and the execution bridge each re-check the link independently rather than
trusting that owning the batch implies owning what it points to.

Route coverage is enforced by a test that derives the batch-scoped mutation inventory from
`app.openapi()` rather than a hand-maintained list, so a new batch-scoped route that ships without
an admission case fails closed at test time.

### Where this does not apply

`POST /reports/batch-schedules:run-due` builds its caller context from
`batch_scheduler_caller_context(config, ...)` — the scheduler's own configuration — rather than from
the invoking caller identity. It only materialises new batches and performs no lookup of existing
tenant-scoped state, so it is a creation path rather than a cross-tenant read. A caller cannot
select the tenant a scheduler pass acts for; that assumption is tracked as
[#177](https://github.com/sgajbi/lotus-report/issues/177).

`GET /reports/operations/attention` is deliberately cross-tenant and redacts tenant identifiers
rather than scoping the query.

## Operational discipline

- keep reporting contract ownership separate from upstream domain truth
- use canonical service identity for cross-app validation
- keep request-convention documentation explicit while the surface is mixed
- expose suitability, mandate-control, open tax-lot, and jurisdiction-specific tax gaps explicitly
  until governed upstream sources provide them; holdings and transaction source-product/trust
  metadata is preserved in report evidence, transaction-level realized gain/loss is sourced from
  lotus-core where present, and summary P&L uses source-backed component status rather than
  synthetic market-value deltas
