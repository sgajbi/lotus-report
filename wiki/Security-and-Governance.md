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

## Operational discipline

- keep reporting contract ownership separate from upstream domain truth
- use canonical service identity for cross-app validation
- keep request-convention documentation explicit while the surface is mixed
- expose suitability, mandate-control, open tax-lot, and jurisdiction-specific tax gaps explicitly
  until governed upstream sources provide them; holdings and transaction source-product/trust
  metadata is preserved in report evidence, transaction-level realized gain/loss is sourced from
  lotus-core where present, and summary P&L uses source-backed component status rather than
  synthetic market-value deltas
