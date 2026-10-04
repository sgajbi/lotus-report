# Mesh Data Products

## Mesh role

`lotus-report` is a maturity-wave producer with `certification_candidate` posture in the Lotus
enterprise data mesh.

## Governed product

- Product ID: `lotus-report:ClientReportEvidencePack:v1`
- Product role: client-report evidence pack for customer/operator evidence workflows and Workbench discovery
- Source declaration: `contracts/domain-data-products/lotus-report-products.v1.json`
- Trust telemetry: `contracts/trust-telemetry/client-report-evidence-pack.telemetry.v1.json`
- Report-wide reconciliation:
  [versioned posture contract](https://github.com/sgajbi/lotus-report/blob/main/contracts/domain-data-products/lotus-report-reconciliation-posture.v1.json)
- Certification boundary: core `lotus-core` evidence is governed through current repo-native
  consumer declarations; analytics-enriched performance/risk evidence is partial and blocked for
  mesh certification until `lotus-performance` and `lotus-risk` producer declarations approve
  `lotus-report` as a governed consumer.

Report-wide `unknown` / `no_reconciliation_policy_established` is deliberate outcome B.
Report composes evidence but owns no book of record, authoritative report-wide comparand,
common comparison cut, tolerance or retained assembled-report check. Ready sections,
independently reconciled sources, canonical revision identity and faithful replay or rerender
cannot establish a report-wide verdict. Retained reuse preserves the unknown status and reason.

Certification remains separate under the already decided
[Platform #780](https://github.com/sgajbi/lotus-platform/issues/780). Promotion still requires a
defined and proven authoritative reconciliation policy and a blocking platform gate from exact
producer main, alongside unchanged mesh SLO, access, source-approval and evidence requirements.
The posture contract does not extend the shared producer schema or approve certification.

## Idea evidence intake route foundation

- Contract: `contracts/idea-evidence-intake/lotus-report-idea-evidence-pack-intake.v1.json`
- Retention authority:
  `contracts/idea-evidence-intake/lotus-report-idea-evidence-retention-policy.v1.json`
- Producer boundary: `lotus-idea` may provide reviewed opportunity evidence packets through the
  implemented source-safe intake route.
- Report boundary: `lotus-report` remains the owner of report materialization and
  `ClientReportEvidencePack` product truth.
- Current status: implemented route foundation and not certified. `POST /reports/idea-evidence-packs`
  proves only source-safe route intake with durable idempotency records in the
  `IDEA_EVIDENCE_INTAKE_LEDGER_PATH` SQLite ledger.
- Materialization status: `POST /reports/idea-evidence-packs/materializations` creates the
  report-owned proof-pack job, preserves immutable lineage to `lotus-idea`, and returns a typed
  source-safe receipt with `report_package_identity`, source authority, render/archive outcome
  posture, optional render/archive identifiers, evidence refs, and remaining blockers. It is still
  not certified for client publication or supported-feature promotion.
- Recovery status: `GET /reports/idea-evidence-packs/materializations` returns that Report-owned
  receipt after an uncertain response only when the tenant, idempotency key and complete persisted
  Idea/portfolio identity match. It never retries the POST and does not add publication authority.
- Policy enforcement: Report rejects unknown, inactive, producer-unauthorized, or tenant-mismatched
  retention references before durable intake or report-job creation. Active legal holds are
  propagated for Archive enforcement and do not grant publication authority.

## Platform relationship

`lotus-platform` aggregates the repo-native declaration, validates trust telemetry, applies mesh SLO/access/evidence policies, and includes this product in generated catalog, dependency graph, live certification, maturity matrix, evidence packs, and RFC-0092 operating reports.

## Operating rule

Report evidence packs must preserve customer-safe versus operator-only evidence boundaries. Do not expose restricted telemetry paths, source artifacts, or entitlement details in public customer evidence.
