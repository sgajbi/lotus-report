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
| `lotus-report.reporting.portfolio_review` | `POST /reports/portfolios/{portfolio_id}/review` | `src/app/routers/reports.py`, `src/app/services/reporting_read_service.py`, `tests/unit/test_reporting_read_service.py`, `tests/integration/test_api.py`, `tests/e2e/test_reporting_workflows.py` | Current portfolio review report route. The route now carries the RFC-0002 first-class meeting-pack contract while preserving legacy report groups during rollout. |
| `lotus-report.reporting.portfolio_review.first_class.v1` | `POST /reports/portfolios/{portfolio_id}/review` typed request/response | `src/app/models/contracts.py`, `src/app/routers/reports.py`, `tests/integration/test_api.py`, `tests/e2e/test_reporting_workflows.py` | Versioned `PortfolioReviewReportRequest` and `PortfolioReviewReportResponse` with OpenAPI-governed snake_case examples, report metadata, `review_period`, `reporting_currency`, audience posture, and structured disclosures. |
| `lotus-report.reporting.portfolio_review.section_readiness.v1` | Portfolio review report response `client_sections` | `src/app/services/reporting_read_service.py`, `tests/unit/test_reporting_read_service.py`, `tests/unit/test_reporting_read_service_additional.py` | Ordered client section envelope with `ready`, `partial`, `unavailable`, `omitted_by_request`, and `not_applicable` states plus normalized machine-readable section items for measures, allocation buckets, performance periods, risk periods, income/activity, holdings, and categorized transactions. |
| `lotus-report.reporting.portfolio_review.supportability.v1` | Portfolio review report response `readiness`, section status, `performance.supportability`, and `riskAnalytics.supportability` | `src/app/services/reporting_read_service.py`, `src/app/services/portfolio_review_advisor.py`, `tests/unit/test_reporting_read_service.py`, `tests/unit/test_reporting_read_service_additional.py`, `tests/unit/test_portfolio_review_advisor.py` | Source-backed supportability model that marks benchmark-dependent performance and risk output as partial when benchmark return series are not sourced, prioritizes blocking and warning notes over informational notes, and carries advisor-useful limitation language. |
| `lotus-report.reporting.portfolio_review.key_figures.v1` | Portfolio review report response `key_figures`, `review_observations`, and `report_coverage` | `src/app/models/contracts.py`, `src/app/services/reporting_read_service.py`, `src/app/routers/integration.py`, `tests/unit/test_reporting_read_service.py`, `tests/unit/test_reporting_read_service_additional.py`, `tests/integration/test_api.py` | Normalized front-office key figures for portfolio value, allocation, performance, risk, income/activity, holdings concentration, transaction categories, position P&L coverage, and contribution coverage. Also identifies unavailable gold-standard figures such as benchmark-relative metrics, targets/guidelines/suitability, and tax-lot realized gain/loss when not sourced. |
| `lotus-report.reporting.portfolio_review.position_pnl.v1` | Portfolio review response `holdings`, `client_sections`, `key_figures.holdings`, and `report_coverage` | `src/app/services/reporting_read_service.py`, `tests/unit/test_reporting_read_service.py`, `tests/unit/test_reporting_read_service_additional.py` | Sourced lotus-core HoldingsAsOf position enrichment including ISIN, product type, sector, country of risk, rating, liquidity tier, held-since date, market price, cost basis, local and reporting-currency market value, local and reporting-currency unrealized P&L, and unrealized P&L percentage. |
| `lotus-report.reporting.portfolio_review.performance_contribution.v1` | Portfolio review response `performance.contribution`, `client_sections`, `key_figures.performance`, and `report_coverage` | `src/app/clients/performance_client.py`, `src/app/services/reporting_read_service.py`, `tests/unit/test_clients.py`, `tests/unit/test_reporting_read_service.py`, `tests/unit/test_reporting_read_service_additional.py` | Sourced lotus-performance YTD contribution view with total contribution, top position contributors, asset-class and sector hierarchy contribution rows, contribution coverage, and matching holding-row contribution enrichment when positions are in the same response. |
| `lotus-report.reporting.portfolio_review.client_profile.v1` | Portfolio review response `client_profile`, `key_figures.client_profile`, `report_coverage`, and `evidence.source_refs` | `src/app/clients/core_query_client.py`, `src/app/services/reporting_read_service.py`, `src/app/models/contracts.py`, `tests/unit/test_clients.py`, `tests/unit/test_reporting_read_service.py` | Sourced lotus-core portfolio detail context for client id, advisor id, booking center, objective, risk exposure, investment horizon, mandate type, leverage permission, portfolio status, open date, base currency, and cost-basis method. Missing profile fields are explicit gaps rather than inferred client facts. |
| `lotus-report.reporting.portfolio_review.advisor_briefing.v1` | Portfolio review response `advisor_briefing` and `report_structure` | `src/app/services/reporting_read_service.py`, `src/app/models/contracts.py`, `tests/unit/test_reporting_read_service.py` | Deterministic advisor-only meeting briefing and presentation sequence that organizes the report around client context, portfolio snapshot, performance drivers, risk controls, activity, appendices, evidence, and required advisor checks. |
| `lotus-report.reporting.portfolio_review.ai_readiness.v1` | Portfolio review response `ai_readiness` and `report_coverage` | `src/app/services/reporting_read_service.py`, `src/app/models/contracts.py`, `tests/unit/test_reporting_read_service.py` | Guarded AI-readiness metadata for grounded meeting assistance. It does not generate advice; trade recommendations, suitability determinations, and inferred client profiles remain blocked until governed `lotus-ai`, advisory evidence, and human approval are in place. |
| `lotus-report.reporting.portfolio_review.upstream_capability_audit.v1` | Portfolio review response `upstream_capability_audit` | `src/app/services/reporting_read_service.py`, `src/app/models/contracts.py`, `tests/unit/test_reporting_read_service.py`, `tests/integration/test_api.py` | Machine-readable certification audit separating source-backed report capabilities from upstream gaps such as benchmark return series, target allocation and suitability controls, tax-lot realized gain/loss, and source-backed risk-free rate availability. |
| `lotus-report.reporting.portfolio_review.evidence_pack.v1` | Portfolio review report response `evidence` | `src/app/services/reporting_read_service.py`, `contracts/domain-data-products/lotus-report-products.v1.json`, `tests/unit/test_domain_data_product_contracts.py`, `tests/unit/test_reporting_read_service.py` | `ClientReportEvidencePack` lineage bundle id, source refs, trust metadata, and domain-product completeness alignment. |
| `lotus-report.reporting.portfolio_review.advisor_sections.v1` | `POST /reports/portfolios/{portfolio_id}/review` response `advisor_sections` | `src/app/services/portfolio_review_advisor.py`, `src/app/services/reporting_read_service.py`, `src/app/models/contracts.py`, `tests/unit/test_reporting_read_service.py`, `tests/unit/test_reporting_read_service_additional.py` | Advisor-only deterministic discussion prompts and non-mutating Workbench, performance, risk, proposal, and action-register route targets. |
| `lotus-report.reporting.portfolio_review.workbench_ready.v1` | Gateway-facing portfolio review route and route targets | `src/app/services/portfolio_review_advisor.py`, `sgajbi/lotus-gateway#145`, `tests/integration/test_api.py` | Gateway-readiness proof for Workbench consumers. No Workbench UI preview is shipped yet; advisor-only material remains separated at the report and gateway boundaries. |
| `lotus-report.aggregation.portfolio_snapshot` | `GET /aggregations/portfolios/{portfolio_id}` | `src/app/routers/aggregations.py`, `src/app/services/aggregation_service.py`, `tests/unit/test_aggregation_service.py`, `tests/integration/test_api.py` | Current reporting aggregation snapshot capability. |
| `lotus-report.integration.capabilities` | `GET /integration/capabilities` | `src/app/routers/integration.py`, `src/app/models/contracts.py`, `tests/integration/test_api.py` | Publishes current feature and workflow posture for downstream consumers. |

## Planned RFC-0002 Feature Candidates

No RFC-0002 feature candidates remain in planned state after RFC-0002 implementation. Future
portfolio review extensions must start as planned rows here and move to implementation-backed only
after code, tests, API contract evidence, and operational validation exist.

## Maintenance Rules

1. Update this file in the same PR that changes feature support.
2. Keep implementation-backed rows tied to concrete code and test evidence.
3. Keep planned RFC work separate from shipped behavior.
4. Keep wiki pages concise and link here instead of duplicating this table.
5. Keep `GET /integration/capabilities` aligned with this file.
