# lotus-report wiki

`lotus-report` is the reporting and aggregation service in Lotus.

## Start here

- Repo entrypoint: [README.md](../README.md)
- Repo context: [REPOSITORY-ENGINEERING-CONTEXT.md](../REPOSITORY-ENGINEERING-CONTEXT.md)
- Local ownership guidance:
  [docs/standards/data-model-ownership.md](../docs/standards/data-model-ownership.md)

## Repo role

This repo owns:

- reporting read-model aggregation
- portfolio summary and portfolio review payload shaping
- report metadata and download-reference contracts
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
- [Getting Started](Getting-Started)
- [Development Workflow](Development-Workflow)
- [Validation and CI](Validation-and-CI)
- [Operations Runbook](Operations-Runbook)
- [Integrations](Integrations)
- [Security and Governance](Security-and-Governance)
- [RFC Index](RFC-Index)
- [Roadmap](Roadmap)
- [Troubleshooting](Troubleshooting)
