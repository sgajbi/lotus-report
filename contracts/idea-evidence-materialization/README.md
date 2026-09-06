# Lotus Report Idea Evidence Materialization Contract

This directory records the report-owned materialization contract for reviewed
`lotus-idea` evidence packs.

The materialization route is separate from the intake-only route:

| Route | Purpose | Support posture |
| --- | --- | --- |
| `POST /reports/idea-evidence-packs` | Source-safe intake and idempotent handoff tracking | Implemented, not certified, no report job |
| `POST /reports/idea-evidence-packs/materializations` | Report-owned proof-pack job creation through snapshot, render, and archive lifecycle | Implemented, not certified, no client publication |
| `GET /reports/idea-evidence-packs/materializations` | Exact, read-only recovery after an uncertain POST response | Implemented, not certified, never resubmits work |

`lotus-report` owns report materialization and `ClientReportEvidencePack`
truth. `lotus-idea` remains the evidence producer, `lotus-render` owns rendered
output creation, and `lotus-archive` owns archive record creation.

The response is a source-safe materialization receipt. It extends the report job
handle with `report_package_identity`, `source_authority`,
`materialization_status`, positive `source_event_version`, report-job/render/archive creation posture, optional
`render_job_id` and `archive_document_id`, evidence refs, and explicit
remaining blockers. It never returns raw idea evidence payloads and never
promotes client-publication authority or supported-feature status.

Recovery requires the original idempotency key plus the exact evidence-pack,
conversion-intent, candidate, evidence-packet, evidence-fingerprint, and
portfolio identities. Report scopes the bounded lookup to the caller tenant and
accepts only the `lotus-idea` application with
`report.idea-materialization.recover`. A missing tenant-scoped record returns
`404`; a changed, ambiguous, malformed, or internally inconsistent identity
returns `409`. The route reads current Report-owned status and never calls the
materialization command. `source_event_version` is derived from the committed append-only Report
status-event sequence: exact replay preserves it and later Report lifecycle evidence increases it.
Status and version are projected atomically, so a concurrent transition cannot produce a torn
receipt. Local headers are contract proof only; production
identity and capability attestation remain environment-owned certification.

Validate locally:

```powershell
make idea-evidence-materialization-contract-gate
```

The gate proves materialization and recovery contract shape, exact identity,
source-authority boundaries, and remaining blockers. It does not grant suitability, advisory proposal,
mandate approval, execution, distribution, client-publication authority, or
supported-feature promotion.
