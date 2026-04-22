# Supported Features

This file is the durable repository source for implementation-backed `lotus-report` product
capabilities.

It separates shipped behavior from planned RFC work. Do not describe a feature as
implementation-backed unless code, tests, API contract, documentation, and operational evidence
exist.

## Support States

| State | Meaning |
| --- | --- |
| `implementation-backed` | The feature is implemented, tested, documented, and reflected by the relevant API/capability surface. |
| `planned` | The feature is proposed or accepted in an RFC but is not yet shipped behavior. |
| `deprecated` | The feature still exists but is scheduled for removal or replacement. |
| `not-supported` | The feature is explicitly outside current product scope. |

## Implementation-Backed Features

| Feature key | Surface | Evidence | Notes |
| --- | --- | --- | --- |
| `lotus-report.reporting.portfolio_summary` | `POST /reports/portfolios/{portfolio_id}/summary` | `src/app/routers/reports.py`, `src/app/services/reporting_read_service.py`, `tests/integration/test_api.py` | Current reporting summary aggregation capability. |
| `lotus-report.reporting.portfolio_review` | `POST /reports/portfolios/{portfolio_id}/review` | `src/app/routers/reports.py`, `src/app/services/reporting_read_service.py`, `tests/unit/test_reporting_read_service.py`, `tests/integration/test_api.py`, `tests/e2e/test_reporting_workflows.py` | Current legacy portfolio review aggregation capability. This is not yet the RFC-0002 first-class meeting-pack contract. |
| `lotus-report.reporting.portfolio_review.advisor_sections.v1` | `POST /reports/portfolios/{portfolio_id}/review` response `advisor_sections` | `src/app/services/portfolio_review_advisor.py`, `src/app/services/reporting_read_service.py`, `src/app/models/contracts.py`, `tests/unit/test_reporting_read_service.py`, `tests/unit/test_reporting_read_service_additional.py` | Advisor-only deterministic discussion prompts and non-mutating Workbench, performance, risk, proposal, and action-register route targets. |
| `lotus-report.aggregation.portfolio_snapshot` | `GET /aggregations/portfolios/{portfolio_id}` | `src/app/routers/aggregations.py`, `src/app/services/aggregation_service.py`, `tests/unit/test_aggregation_service.py`, `tests/integration/test_api.py` | Current reporting aggregation snapshot capability. |
| `lotus-report.integration.capabilities` | `GET /integration/capabilities` | `src/app/routers/integration.py`, `src/app/models/contracts.py`, `tests/integration/test_api.py` | Publishes current feature and workflow posture for downstream consumers. |

## Planned RFC-0002 Feature Candidates

These candidates are planned only. They must not be added to `GET /integration/capabilities` as
enabled features until implementation evidence exists.

| Feature key | Target surface | Required before implementation-backed |
| --- | --- | --- |
| `lotus-report.reporting.portfolio_review.first_class.v1` | `POST /reports/portfolios/{portfolio_id}/review` | Typed `PortfolioReviewReportResponse`, OpenAPI examples, contract tests, integration tests, docs. |
| `lotus-report.reporting.portfolio_review.section_readiness.v1` | Portfolio review report response | Ready, partial, unavailable, omitted-by-request, and not-applicable states with reason codes and tests. |
| `lotus-report.reporting.portfolio_review.evidence_pack.v1` | Portfolio review report response and/or evidence-pack route | Source refs, lineage bundle id, trust metadata, data product declaration alignment, `make domain-product-validate`. |
| `lotus-report.reporting.portfolio_review.workbench_ready.v1` | Gateway and Workbench consumption | Stable gateway contract, Workbench-ready route targets, cross-repo validation evidence where touched. |

## Maintenance Rules

1. Update this file in the same PR that changes feature support.
2. Keep implementation-backed rows tied to concrete code and test evidence.
3. Keep planned RFC work separate from shipped behavior.
4. Keep wiki pages concise and link here instead of duplicating this table.
5. Keep `GET /integration/capabilities` aligned with this file.
