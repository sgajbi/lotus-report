# Report Ordering

`lotus-report` publishes the business choices that a reporting product may offer through
`GET /integration/report-ordering-catalogue`.

The catalogue is the source of truth for report families, ordering modes, output formats,
configuration fields, selectable sections, release posture, and current rendering availability.
Gateway and Workbench consumers must use this contract instead of maintaining their own report
lists or template rules.

## Business use

The first-wave catalogue supports two distinct operating models:

1. a client advisor can request a portfolio review for the selected portfolio,
2. reporting operations can materialize the same portfolio review for an explicit portfolio batch
   or a governed reporting schedule,
3. portfolio-management evidence reports are created only from their approved source workflows.

Report creation is not client distribution. A generated or archived document still requires the
separate, implementation-backed release and distribution controls owned outside `lotus-report`.

## Published report families

| Business report | Intended use | Available ordering | Release posture |
| --- | --- | --- | --- |
| Portfolio review report | advisor and portfolio-manager review of one client portfolio | single portfolio, explicit portfolio batch, governed schedule | advisor review required; distribution not supported |
| Pre-trade decision evidence | investment-control evidence from an approved portfolio-management decision | source workflow only | internal control only |
| Rebalance wave evidence | operational and audit evidence for a managed rebalance wave | source workflow only | internal control only |
| Post-trade outcome review | review of realised outcomes against approved pre-trade evidence | source workflow only | internal control only |

The source-workflow reports are visible so consumers can explain product coverage, but they are not
interactive advisor ordering choices. Their source applications remain responsible for creating
the bounded evidence payload that Report accepts.

## Portfolio review configuration

The portfolio review catalogue publishes these business inputs:

| Input | Requirement | Defaulting and source rule |
| --- | --- | --- |
| Report date | required | supplied by the caller |
| Reporting currency | optional | selected portfolio currency when omitted |
| Comparison benchmark | optional | portfolio benchmark when omitted; Gateway resolves the eligible benchmark list |
| Allocation views | optional | asset class when omitted; available values come from the catalogue |
| Report sections | required and optional choices | labels, display order, defaults, and dependencies come from the catalogue |

The client and mandate profile is always included. Optional sections cover portfolio overview,
allocation and portfolio construction, performance review, risk review, income/cash/activity,
holdings detail, and transaction activity.

Unknown formats, sections, allocation views, or configuration fields are rejected before Report
creates a durable job or batch. Governed schedule configuration uses the same validation policy.

## Availability and supportability

Structured data packages are Report-owned and remain available when the report family is otherwise
supported. PDF availability is derived from live `lotus-render` metadata and is ready only when:

- Render reports ready supportability,
- deterministic output is supported,
- the template registry is ready,
- the runtime is available,
- PDF is a supported output format.

If Render evidence is missing, malformed, degraded, or unavailable, the catalogue keeps structured
data truth separate and marks PDF as partial or unavailable with a bounded reason. Consumers must
show that posture and must not offer a disabled format as ready.

## Ownership boundary

| Owner | Responsibility |
| --- | --- |
| `lotus-report` | report-family definitions, business configuration, submission validation, structured data, and Report-side supportability |
| `lotus-render` | deterministic document runtime and PDF supportability evidence |
| `lotus-gateway` | caller entitlement, advisor-book scope, selected-portfolio eligibility, eligible benchmark projection, and the product-facing API |
| `lotus-workbench` | business workflow, portfolio context, accessible labels, disabled-state explanation, confirmation, and status follow-up |
| `lotus-archive` | document persistence and archive lifecycle; archive handoff does not imply distribution authority |

Workbench must consume the Gateway projection planned in
[`lotus-gateway#499`](https://github.com/sgajbi/lotus-gateway/issues/499). It must not call Report
directly or hard-code report families while that consumer contract remains unmerged.

## Direct service inspection

Use direct Report access only for governed development or operator inspection:

```bash
curl "http://127.0.0.1:8300/integration/report-ordering-catalogue" \
  -H "X-Actor-Id: support-operator-1" \
  -H "X-Tenant-Id: tenant-sg" \
  -H "X-Role: support_operator" \
  -H "X-Correlation-ID: report-catalogue-local-proof"
```

Check the top-level `supportability`, then evaluate each report family's `ordering_modes`,
`output_formats`, `configuration_fields`, `sections`, and family-level `supportability` before
presenting a choice.

## Change control

Add or change a report choice only when:

1. a GitHub issue records the business use and source ownership,
2. submission and data-package behavior are implemented,
3. Render and Archive posture are described without overclaiming distribution,
4. the shared catalogue definitions and validators are updated together,
5. unit, integration, OpenAPI, repository, and exact-main runtime evidence pass,
6. supported-feature, repository-context, and wiki truth are updated in the same delivery.
