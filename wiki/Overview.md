# Overview

## Business role

`lotus-report` composes reporting-ready portfolio summary and review payloads from authoritative
upstream services. It is the reporting contract owner, not the owner of the underlying ledger or
analytics truth.

## Ownership boundaries

This repo owns:

1. reporting aggregation and payload composition
2. report metadata and download-reference contracts
3. capability publication for reporting flows

This repo does not own:

1. source portfolio truth, which belongs to `lotus-core`
2. performance methodology authority, which belongs to `lotus-performance`
3. risk methodology authority, which belongs to `lotus-risk`

## Current posture

- orchestration-heavy service in the canonical front-office stack
- exposed through `report.dev.lotus`
- `POST /reports/portfolios/{portfolio_id}/review` is the RFC-0002 first-class review report
  contract with typed request/response models, client/advisor section separation, readiness states,
  and evidence lineage
- lighter CI than some core domain services, but still under the Lotus lane model
- mixed request-convention surface that must be documented carefully
