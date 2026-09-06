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
- Separate store: `idea_evidence_intake` is durable Report-owned state, and is
  the last Report store still on a DIFFERENT engine by default - SQLite at
  `IDEA_EVIDENCE_INTAKE_LEDGER_PATH`, written by
  `src/app/idea_evidence_intake/service.py`. An audit of Report-owned state
  that stops at the PostgreSQL ledgers is incomplete while that remains true.
  **Migrating (report#326).** A PostgreSQL home exists - migration 024, with
  column types and indexes asserted by `report_schema_upgrade_check.py` inside
  the existing migration smoke - and `PostgresIdeaEvidenceIntakeLedger`
  implements the same surface, selected by
  `REPORT_IDEA_EVIDENCE_INTAKE_LEDGER_BACKEND`.
  **The default is still `sqlite`, and the gap is still open.** Nothing has
  transferred existing records into PostgreSQL, and starting a deployment from
  an empty intake ledger is the unverifiable-replay state report#334 refuses:
  the report rows survive, the intake evidence does not, and no replay can then
  be told apart from a first submission. Until the transfer is delivered and
  the default changes, read the migration contract as covering the PostgreSQL
  *table* but not the store that production actually uses.
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
