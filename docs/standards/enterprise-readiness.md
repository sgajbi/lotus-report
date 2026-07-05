# Enterprise Readiness Baseline (lotus-report)

- Standard reference: `lotus-platform/Enterprise Readiness Standard.md`
- Scope: reporting/aggregation read and export APIs consuming lotus-core/lotus-performance sources.
- Change control: RFC for standard changes, ADR for temporary deviations.

## Security and IAM Baseline

- Write-path/privileged action audit middleware is enabled.
- Direct local debugging is the only permissive runtime posture. Set
  `ENTERPRISE_RUNTIME_PROFILE=local` for direct process debugging on `127.0.0.1:8300`.
- Production-like profiles (`prod`, `production`, `preprod`, `staging`, and `uat`) fail closed:
  write and read authorization are enforced even if the authz toggles are omitted, and runtime
  validation fails unless `ENTERPRISE_ENFORCE_AUTHZ=true`,
  `ENTERPRISE_ENFORCE_READ_AUTHZ=true`, and `ENTERPRISE_PRIMARY_KEY_ID` are configured.
- Read and write authorization require caller audit headers plus either `X-Service-Identity` or
  `Authorization` whenever the matching enforcement toggle is enabled or the runtime profile is
  production-like.
- Read-path audit events can be enabled with `ENTERPRISE_AUDIT_READS=true`; emitted metadata stays
  identifier-only and records status code plus `access_type=read`.
- Capability rules in `ENTERPRISE_CAPABILITY_RULES_JSON` apply to both read and write paths when
  the matching enforcement toggle is enabled or the runtime profile is production-like.
- Audit metadata includes actor/tenant/role/correlation with sensitive-field redaction.

Evidence:
- `src/app/enterprise_readiness.py`
- `src/app/main.py`
- `tests/unit/test_enterprise_readiness.py`

## API Governance Baseline

- OpenAPI contracts are versioned and enforced through CI conformance checks.
- Compatibility/deprecation policy follows platform RFC governance.

Evidence:
- `src/app/main.py`
- `tests/integration`

## Configuration and Feature Management Baseline

- Feature flags are centrally loaded from environment JSON.
- Tenant/role scoping is deterministic and deny-by-default for missing/invalid config.

Evidence:
- `src/app/enterprise_readiness.py`
- `tests/unit/test_enterprise_readiness.py`

## Data Quality and Reconciliation Baseline

- Reporting payload shaping includes validation and explicit failure on invalid upstream data.
- Reconciliation expectations are documented with durability standards.

Evidence:
- `src/app/services`
- `docs/standards/durability-consistency.md`

## Reliability and Operations Baseline

- Resilient upstream clients, health/readiness probes, and migration/runbook conventions are in place.
- `GET /integration/capabilities` publishes RFC-0108 evidence-surface supportability for reporting
  evidence packs, report jobs, snapshots, upstream lineage, render/archive handoff, and replay
  operations. `/metrics` emits `lotus_report_evidence_surface_supportability_total` with bounded
  `state`, `reason`, and `freshness_bucket` labels only.

Evidence:
- `src/app/clients.py`
- `src/app/routers/integration.py`
- `src/app/reporting_metrics.py`
- `docs/standards/scalability-availability.md`
- `docs/standards/migration-contract.md`

## Privacy and Compliance Baseline

- Redaction and audit traceability applied for critical actions.

Evidence:
- `src/app/enterprise_readiness.py`
- `tests/unit/test_enterprise_readiness.py`

## Deviations

- Deviations require ADR with mitigation and expiry review date.
