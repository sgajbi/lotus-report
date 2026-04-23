# lotus-report wiki

`lotus-report` is the reporting and aggregation service in Lotus.

## Start here

- Repo entrypoint: [README.md](../README.md)
- Repo context: [REPOSITORY-ENGINEERING-CONTEXT.md](../REPOSITORY-ENGINEERING-CONTEXT.md)
- Local ownership guidance:
  [docs/standards/data-model-ownership.md](../docs/standards/data-model-ownership.md)

## Current phase

- active reporting orchestration service in the canonical front-office stack
- portfolio review report is live as a machine-readable client/advisor meeting-pack contract
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
- [Getting Started](Getting-Started)
- [Development Workflow](Development-Workflow)
- [Validation and CI](Validation-and-CI)
- [Operations Runbook](Operations-Runbook)
- [Integrations](Integrations)
- [Security and Governance](Security-and-Governance)
- [RFC Index](RFC-Index)
- [Roadmap](Roadmap)
- [Troubleshooting](Troubleshooting)
