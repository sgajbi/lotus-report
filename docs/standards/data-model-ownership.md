# Data Model Ownership

- Service: `lotus-report`
- Ownership status: **durable reporting-lifecycle entities, no upstream domain entities.**
  Report persists the job lifecycle and its evidence - `report_job`, `report_job_work_item`,
  `report_status_event`, `report_input_snapshot`, `report_upstream_call`,
  `report_job_relationship`, `report_rerender_attempt`, `report_request`, `report_batch`,
  `report_batch_item`, `report_batch_schedule_definition` and
  `report_batch_schedule_audit`, all in the PostgreSQL job and batch ledgers.
  It persists no portfolio, position, transaction, valuation or performance
  entity; those are read from their owners and echoed.
- Separate store: `idea_evidence_intake` is durable Report-owned state on a
  DIFFERENT engine - SQLite at `IDEA_EVIDENCE_INTAKE_LEDGER_PATH`, created and
  written by `src/app/idea_evidence_intake/service.py`. It carries its own
  persistence, retention and migration boundary, so an audit of Report-owned
  state that stops at the PostgreSQL ledgers is incomplete.
- Domain responsibility: reporting orchestration and aggregation payload shaping.

## Service Boundaries

- Core portfolio data comes from lotus-core APIs.
- Advanced analytics comes from lotus-performance APIs.
- This service owns reporting contracts, aggregation composition logic, and the durable
  reporting lifecycle listed above.

## Naming and Vocabulary Rules

- Follow platform glossary terms from `lotus-platform/Domain Vocabulary Glossary.md`.
- Do not introduce service-local synonyms for canonical portfolio, position, transaction, valuation, or performance terms.

## Scope Of This Document

This is a boundary and vocabulary note, not a field-level ownership map. It answers which
SERVICE owns a kind of data; it does not enumerate individual fields. Field-level ownership
for a specific contract is answered by that contract's schema and by the upstream owner's
published contract, not here.
