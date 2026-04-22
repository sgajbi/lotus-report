# RFC Traceability Map

This document provides explicit implementation evidence pointers for active RFCs.

## RFC-0001 - Test Pyramid Rebalance and Meaningful Coverage Hardening

- Test pyramid and coverage evidence:
  - `tests/unit/`
  - `tests/integration/`
  - `tests/e2e/`
  - `Makefile` (`test-pyramid`, `test-coverage`, `ci`)
  - `.github/workflows/pr-merge-gate.yml`

## RFC-0002 - First-Class Portfolio Review Report Endpoint

- Planning and research evidence:
  - `rfcs/RFC-0002-first-class-portfolio-review-report-endpoint.md`
  - `docs/supported-features.md`
  - `src/app/routers/reports.py` (`/reports/portfolios/{portfolio_id}/review`)
  - `src/app/services/reporting_read_service.py`
  - `contracts/domain-data-products/lotus-report-products.v1.json`
- Implementation evidence added during rollout:
  - typed report request/response contracts in `src/app/models/contracts.py`
  - review route contract tests in `tests/integration/test_api.py`
  - implementation-backed capability publication in `GET /integration/capabilities`
  - section, performance, risk, evidence, and lineage tests in
    `tests/unit/test_reporting_read_service.py` and
    `tests/unit/test_reporting_read_service_additional.py`
  - advisor discussion builder tests in `tests/unit/test_portfolio_review_advisor.py`
  - gateway readiness proof in `sgajbi/lotus-gateway#145`
  - OpenAPI/vocabulary validation output
  - supported-features rows promoted from `planned` to `implementation-backed` only after
    implementation evidence exists
  - repo-local wiki usage examples
