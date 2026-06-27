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
