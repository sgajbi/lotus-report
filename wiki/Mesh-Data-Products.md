# Mesh Data Products

## Mesh role

`lotus-report` is a maturity-wave producer in the Lotus enterprise data mesh.

## Governed product

- Product ID: `lotus-report:ClientReportEvidencePack:v1`
- Product role: client-report evidence pack for customer/operator evidence workflows and Workbench discovery
- Source declaration: `contracts/domain-data-products/lotus-report-products.v1.json`
- Trust telemetry: `contracts/trust-telemetry/client-report-evidence-pack.telemetry.v1.json`
- Certification boundary: core `lotus-core` evidence is governed through current repo-native
  consumer declarations; analytics-enriched performance/risk evidence is partial and blocked for
  mesh certification until `lotus-performance` and `lotus-risk` producer declarations approve
  `lotus-report` as a governed consumer.

## Idea evidence intake route foundation

- Contract: `contracts/idea-evidence-intake/lotus-report-idea-evidence-pack-intake.v1.json`
- Producer boundary: `lotus-idea` may provide reviewed opportunity evidence packets through the
  implemented source-safe intake route.
- Report boundary: `lotus-report` remains the owner of report materialization and
  `ClientReportEvidencePack` product truth.
- Current status: implemented route foundation and not certified. `POST /reports/idea-evidence-packs`
  proves only source-safe route intake with durable idempotency records in the
  `IDEA_EVIDENCE_INTAKE_LEDGER_PATH` SQLite ledger; materialization, rendered output, archive
  record, client-publication authority, and supported feature promotion remain separate
  certification work.

## Platform relationship

`lotus-platform` aggregates the repo-native declaration, validates trust telemetry, applies mesh SLO/access/evidence policies, and includes this product in generated catalog, dependency graph, live certification, maturity matrix, evidence packs, and RFC-0092 operating reports.

## Operating rule

Report evidence packs must preserve customer-safe versus operator-only evidence boundaries. Do not expose restricted telemetry paths, source artifacts, or entitlement details in public customer evidence.
