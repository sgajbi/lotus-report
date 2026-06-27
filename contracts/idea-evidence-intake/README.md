# Lotus Report Idea Evidence Intake Contract

This directory records the report-owned contract posture for source-safe
`lotus-idea` evidence-pack intake.

`lotus-report` owns report materialization and `ClientReportEvidencePack`
product truth. `lotus-idea` may provide reviewed opportunity evidence as a
producer input, but it does not create report jobs, rendered documents, archive
records, or client-ready publication authority.

Current contract:

1. `lotus-report-idea-evidence-pack-intake.v1.json`
   Implemented, not-certified intake-route contract for reviewed `lotus-idea`
   evidence packs.

Materialization is governed separately in
[`../idea-evidence-materialization`](../idea-evidence-materialization). Keep the
two contracts separate so intake proof cannot be mistaken for report-job,
render, or archive proof.

Validate locally:

```powershell
make idea-evidence-intake-contract-gate
```

This gate proves contract shape, source-authority boundaries, and the bounded
route-foundation posture only. It is not report materialization, render,
archive, client-publication, or supported-feature proof.
