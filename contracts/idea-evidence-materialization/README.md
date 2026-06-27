# Lotus Report Idea Evidence Materialization Contract

This directory records the report-owned materialization contract for reviewed
`lotus-idea` evidence packs.

The materialization route is separate from the intake-only route:

| Route | Purpose | Support posture |
| --- | --- | --- |
| `POST /reports/idea-evidence-packs` | Source-safe intake and idempotent handoff tracking | Implemented, not certified, no report job |
| `POST /reports/idea-evidence-packs/materializations` | Report-owned proof-pack job creation through snapshot, render, and archive lifecycle | Implemented, not certified, no client publication |

`lotus-report` owns report materialization and `ClientReportEvidencePack`
truth. `lotus-idea` remains the evidence producer, `lotus-render` owns rendered
output creation, and `lotus-archive` owns archive record creation.

Validate locally:

```powershell
make idea-evidence-materialization-contract-gate
```

The gate proves materialization contract shape, source-authority boundaries,
and remaining blockers. It does not grant suitability, advisory proposal,
mandate approval, execution, distribution, client-publication authority, or
supported-feature promotion.
