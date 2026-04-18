# Operations Runbook

## Important operational checks

- confirm canonical reporting identity is `report.dev.lotus` before cross-app validation
- treat upstream client failures as reporting-orchestration issues first, not as local formatting bugs
- verify correlation and observability headers on reporting endpoints
- use repo-native gates before inventing ad hoc checks

## Operational truths

- `lotus-report` composes from lotus-core, lotus-performance, and lotus-risk
- reporting payload quality depends on upstream fidelity and contract handling
- direct process port `8300` is useful for local debugging, but canonical cross-app validation
  should use `report.dev.lotus`

## Key references

- [docs/standards/data-model-ownership.md](../docs/standards/data-model-ownership.md)
- [docs/standards/enterprise-readiness.md](../docs/standards/enterprise-readiness.md)
- [docs/standards/migration-contract.md](../docs/standards/migration-contract.md)
- [docs/standards/scalability-availability.md](../docs/standards/scalability-availability.md)
