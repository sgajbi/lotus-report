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
- enterprise readiness and observability behavior are covered by unit tests

## Operational discipline

- keep reporting contract ownership separate from upstream domain truth
- use canonical service identity for cross-app validation
- keep request-convention documentation explicit while the surface is mixed
