# Lotus Report Domain Data Product Declarations

This directory stores `lotus-report` repo-native declarations for governed Lotus domain data
products.

`lotus-report` is a reporting composer. It is not the authority for portfolio, performance, or risk
truth. The current repo-native declaration therefore records the governed upstream products that
`lotus-report` consumes directly from authoritative services.

Current declarations:

1. `lotus-report-consumers.v1.json`
   Consumer declaration for the governed `lotus-core` products used by reporting payloads.
2. `lotus-report-products.v1.json`
   Producer declaration for `ClientReportEvidencePack`, including the first-class portfolio review
   report route once it carries report-level lineage and customer-consumable evidence metadata.
3. `../idea-evidence-intake/lotus-report-idea-evidence-pack-intake.v1.json`
   Implemented, not-certified intake-route contract for reviewed `lotus-idea` evidence packets.
   It proves only source-safe route intake through `POST /reports/idea-evidence-packs`; it is not
   report materialization, rendered output, archive record, client-publication authority, or
   supported-feature proof.
4. `../idea-evidence-materialization/lotus-report-idea-evidence-pack-materialization.v1.json`
   Implemented, not-certified materialization contract for reviewed `lotus-idea` evidence packets.
   It proves report-owned proof-pack job materialization through the existing snapshot, render, and
   archive lifecycle while keeping client publication and supported-feature promotion blocked.

The repository-owned
[`lotus-report-reconciliation-posture.v1.json`](lotus-report-reconciliation-posture.v1.json)
is the normative report-wide reconciliation sidecar for the review API's `evidence` contract.
Outcome B deliberately retains `unknown` / `no_reconciliation_policy_established`: Report
composes evidence but owns no book of record, authoritative report-wide comparand, common
comparison cut, tolerance or retained assembled-report check. Ready sections, independently
reconciled sources, revision identity and faithful replay or rerender cannot supply that verdict.
Capture and retained reuse preserve the same status and reason.

Certification remains separate. The product remains `certification_candidate` under
[Platform #780](https://github.com/sgajbi/lotus-platform/issues/780); promotion requires a defined
and proven authoritative reconciliation policy and a blocking platform gate from exact producer
main, alongside existing mesh requirements. This sidecar does not extend the shared producer
schema or change SLO, source-approval, access or evidence gates.

Local validation:

```powershell
python scripts/validate_domain_data_product_contracts.py
```

Make target:

```powershell
make domain-product-validate
```

Idea evidence intake contract validation:

```powershell
make idea-evidence-intake-contract-gate
```

Idea evidence materialization contract validation:

```powershell
make idea-evidence-materialization-contract-gate
```

Current watchlist:

1. `lotus-performance` and `lotus-risk` are live service dependencies in reporting workflows, but
   their current producer declarations do not yet approve `lotus-report` as a governed consumer.
2. Those dependencies should be added only after the upstream producer declarations explicitly
   approve the reporting use case and required trust metadata.
3. Portfolio review responses still identify `lotus-performance` and `lotus-risk` in report-level
   `evidence.source_refs` when those services are used, without upgrading the repo-native consumer
   declaration ahead of producer approval.
4. `ClientReportEvidencePack:v1` therefore publishes core `lotus-core` evidence as governed and
   marks analytics-enriched performance/risk evidence as partially certified until upstream
   producer approval and consumer-declaration updates are complete.
5. Do not set the trust telemetry snapshot to `complete`, `quality_passed`, and unblocked while
   the analytics dependencies remain on this watchlist.
