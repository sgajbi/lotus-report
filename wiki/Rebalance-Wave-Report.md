# Rebalance Wave Report

## Purpose

`POST /reports/rebalance-waves` materializes a manage-owned `DpmWaveReportInput` into a governed
report job. It is the first-wave `lotus-report` implementation for RFC41-WTBD-008 and lets a DPM
rebalance wave move through the same durable report, render, and archive lifecycle as portfolio,
outcome-review, and proof-pack artifacts.

This endpoint is not a wave engine. `lotus-manage` remains the source of wave state, item posture,
proof-pack linkage, supportability, source refs, and internal operations handoff evidence.
`lotus-report` persists the bounded handoff, records lineage, builds the render package, and
orchestrates render/archive state transitions.

## Business Flow

```mermaid
sequenceDiagram
    participant Manage as lotus-manage
    participant Report as lotus-report
    participant Render as lotus-render
    participant Archive as lotus-archive
    participant Ops as Ops / Support

    Manage->>Report: POST /reports/rebalance-waves<br/>DpmWaveReportInput
    Report->>Report: create idempotent rebalance_wave job
    Report->>Report: persist immutable snapshot
    Report->>Report: record lotus-manage lineage
    alt PDF requested
        Report->>Render: submit rebalance-wave render package
        Render-->>Report: rendered artifact + diagnostics
        Report->>Archive: archive rendered wave report
        Archive-->>Report: archive document id
    end
    Ops->>Report: GET job status / snapshot / lineage
```

## Current Feature Coverage

| Capability | Current state |
| --- | --- |
| Job initiation | `POST /reports/rebalance-waves` with required `Idempotency-Key` and governed caller context headers |
| Source input | Manage-owned `DpmWaveReportInput` supplied as `wave_report_input` |
| Snapshot | Immutable `report_input_snapshot` row with contract `dpm_wave_report_input.v1` |
| Lineage | Append-only upstream-call evidence to `lotus-manage` wave report-input source |
| Render package | `report_type=rebalance_wave`, `template_id=rebalance-wave`, `template_version=v1` |
| Archive handoff | Reuses the existing report-to-archive lifecycle after successful PDF render |
| Status and support | Existing job status, event, snapshot, lineage, and diagnostics endpoints |

## Non-Functional Posture

- Idempotency is enforced by the report request ledger and deterministic request hash.
- Snapshot payloads remain durable and hashable; support endpoints expose evidence posture without
  raw source payloads or storage references.
- Correlation and trace identifiers are preserved through snapshot, render, and archive handoff.
- Render determinism is bounded by `lotus-render` runtime-envelope fingerprinting.
- Archive retrieval, retention execution, legal hold, purge, and access-audit execution remain
  `lotus-archive` responsibilities.
- `external_execution_claimed` remains false until a separate OMS/execution owner is implemented
  and certified.

## Ownership Boundary

| Concern | Owner |
| --- | --- |
| Wave state, item posture, proof-pack linkage, supportability, source refs, internal handoff evidence | `lotus-manage` |
| Report job ledger, snapshot, lineage, render package, archive handoff | `lotus-report` |
| PDF execution and render diagnostics | `lotus-render` |
| Archived document identity, retention, legal hold, retrieval, purge, access audit | `lotus-archive` |

## Validation Evidence

Implementation-backed tests cover:

- wave request ledger creation and idempotent identity fields,
- required wave identity and as-of validation,
- wave render-package contract shape,
- `/reports/rebalance-waves` snapshot capture and lineage lookup,
- PDF request handoff to the render orchestration service.
