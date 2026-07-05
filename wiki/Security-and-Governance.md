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
- temporary dependency vulnerability exceptions are governed by
  `docs/standards/dependency-vulnerability-exceptions.json`; `make security-audit` fails when an
  exception is expired, missing ownership, or not linked to a GitHub issue
- enterprise readiness and observability behavior are covered by unit tests
- enterprise read authorization is toggle-backed with `ENTERPRISE_ENFORCE_READ_AUTHZ`; read audit
  events are toggle-backed with `ENTERPRISE_AUDIT_READS`
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
  until governed upstream sources provide them; transaction-level realized gain/loss is sourced
  from lotus-core where present
