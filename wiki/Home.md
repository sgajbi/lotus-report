# lotus-report wiki

`lotus-report` is the reporting and aggregation service in Lotus.

## Start here

- Repo entrypoint: [README.md](https://github.com/sgajbi/lotus-report/blob/main/README.md)
- Repo context: [REPOSITORY-ENGINEERING-CONTEXT.md](https://github.com/sgajbi/lotus-report/blob/main/REPOSITORY-ENGINEERING-CONTEXT.md)
- Local ownership guidance:
  [docs/standards/data-model-ownership.md](https://github.com/sgajbi/lotus-report/blob/main/docs/standards/data-model-ownership.md)

## Current phase

- active reporting orchestration service in the canonical front-office stack
- portfolio review report is live as a machine-readable client/advisor meeting-pack contract
- post-trade outcome-review report jobs are live for manage-owned `DpmOutcomeReportInput`
  snapshots, render-package assembly, and report-to-archive lifecycle handoff
- pre-trade proof-pack report jobs are live for manage-owned `DpmProofPackReportInput` snapshots,
  render-package assembly, and report-to-archive lifecycle handoff
- rebalance-wave report jobs are live for manage-owned `DpmWaveReportInput` snapshots,
  proof-pack posture, internal handoff lineage, render-package assembly, and report-to-archive
  lifecycle handoff
- reviewed `lotus-idea` evidence packs can be materialized into governed proof-pack report jobs
  through `POST /reports/idea-evidence-packs/materializations`, with lineage preserved to
  `lotus-idea`; the response carries report-package identity, render/archive outcome posture, and
  explicit remaining blockers, while client publication remains blocked
- DPM proof-pack, wave, and outcome-review report jobs can carry manage-owned
  `portfolio_memory_context` as bounded lineage for report packages without reconstructing
  portfolio-memory events or source-owner facts
- report jobs expose a report-owned portfolio-memory source-event family for downstream
  ingestion, with stable identities, source refs, artifact refs, hashes, and governance policy
  without raw payloads or storage references
- public request, query, and response fields use canonical snake_case names
- Swagger must reflect shipped API surfaces only, with no stale placeholder endpoints

## Most important commands

- `make install`
- `make check`
- `make ci`
- `make docker-build`

## Repo role

This repo owns:

- reporting read-model aggregation
- portfolio summary and portfolio review payload shaping
- outcome-review report artifact orchestration from manage-owned bounded report input
- proof-pack report artifact orchestration from manage-owned bounded report input
- idea-evidence proof-pack materialization from reviewed `lotus-idea` evidence plus report-owned
  portfolio scope, returning source-safe receipt evidence without client-publication authority
- rebalance-wave report artifact orchestration from manage-owned bounded report input
- report-side portfolio-memory lineage consumption for DPM report packages
- report-owned portfolio-memory source events for report lifecycle, snapshot, render, and archive
  evidence
- reporting capability publication for downstream consumers

This repo does not own:

- canonical portfolio data truth
- authoritative performance analytics
- authoritative risk methodology
- ledger or booking system state

## Navigation

- [Overview](Overview)
- [Architecture](Architecture)
- [API Surface](API-Surface)
- [Portfolio Review Report](Portfolio-Review-Report)
- [Proof-Pack Report](Proof-Pack-Report)
- [Rebalance Wave Report](Rebalance-Wave-Report)
- [Getting Started](Getting-Started)
- [Development Workflow](Development-Workflow)
- [Validation and CI](Validation-and-CI)
- [Operations Runbook](Operations-Runbook)
- [Integrations](Integrations)
- [Security and Governance](Security-and-Governance)
- [RFC Index](RFC-Index)
- [Roadmap](Roadmap)
- [Troubleshooting](Troubleshooting)
