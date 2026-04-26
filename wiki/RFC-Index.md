# RFC Index

## Platform RFCs that matter most here

- RFC-0050
- RFC-0067
- RFC-0071
- RFC-0072
- RFC-0073

## Local standards and guidance

- [docs/standards/data-model-ownership.md](../docs/standards/data-model-ownership.md)
- [docs/standards/enterprise-readiness.md](../docs/standards/enterprise-readiness.md)
- [docs/standards/migration-contract.md](../docs/standards/migration-contract.md)
- [docs/standards/rfc-traceability.md](../docs/standards/rfc-traceability.md)
- [docs/supported-features.md](../docs/supported-features.md)

## Local RFCs

- [RFC-0001: Test Pyramid Rebalance and Meaningful Coverage Hardening](../rfcs/RFC-0001-test-pyramid-rebalance-and-meaningful-coverage-hardening.md)
- [RFC-0002: First-Class Portfolio Review Report Endpoint](../rfcs/RFC-0002-first-class-portfolio-review-report-endpoint.md)
  done; shipped first-class portfolio review report contract for advisor/client meeting workflows

## Current emphasis

- reporting aggregation must stay faithful to upstream contract boundaries
- API vocabulary and OpenAPI quality matter because reporting payloads are consumed cross-app
- CI and migration discipline still apply even though the repo is orchestration-heavy
- supported-feature wording must stay implementation-backed; planned RFC behavior should not be
  presented as shipped product capability
- repo-local wiki pages should stay concise and operator-facing; RFC implementation detail belongs
  in `rfcs/` and implementation-backed feature truth belongs in `docs/supported-features.md`
- RFC-0104 batch reporting is in progress. Internal durable batch and batch-item materialization
  primitives exist for explicit portfolio lists and selected subsets, and deterministic
  schedule-cycle materialization exists for monthly, quarterly, semi-annual, yearly, and explicit
  cycles. Internal dispatch, lease, report-job creation/reuse, and back-pressure primitives now
  exist. Internal bounded retry, pause/resume, cancellation-boundary, and expired-lease recovery
  primitives now exist. Certified batch materialization, status, and control APIs now exist in
  `lotus-report`. Internal item execution can advance a dispatched batch item through the existing
  report-job, snapshot, render, and archive handoff path and reconcile the final batch-item state.
  No batch scheduler loop, worker runtime, dispatch operator API, gateway exposure, or Workbench
  batch surface is shipped yet
