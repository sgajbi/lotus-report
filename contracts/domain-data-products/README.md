# Lotus Report Domain Data Product Declarations

This directory stores `lotus-report` repo-native declarations for governed Lotus domain data
products.

`lotus-report` is a reporting composer. It is not the authority for portfolio, performance, or risk
truth. The current repo-native declaration therefore records the governed upstream products that
`lotus-report` consumes directly from authoritative services.

Current declarations:

1. `lotus-report-consumers.v1.json`
   Consumer declaration for the governed `lotus-core` products used by reporting payloads.

Local validation:

```powershell
python scripts/validate_domain_data_product_contracts.py
```

Make target:

```powershell
make domain-product-validate
```

Current watchlist:

1. `lotus-performance` and `lotus-risk` are live service dependencies in reporting workflows, but
   their current producer declarations do not yet approve `lotus-report` as a governed consumer.
2. Those dependencies should be added only after the upstream producer declarations explicitly
   approve the reporting use case and required trust metadata.
