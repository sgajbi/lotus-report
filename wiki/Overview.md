# Overview

## Business role

`lotus-report` composes reporting-ready portfolio summary and review payloads from authoritative
upstream services. It is the reporting contract owner, not the owner of the underlying ledger or
analytics truth.

## Ownership boundaries

This repo owns:

1. reporting aggregation and payload composition
2. report metadata and download-reference contracts
3. first-class portfolio review meeting-pack contracts for front-office client/advisor reviews
4. capability publication for reporting flows

This repo does not own:

1. source portfolio truth, which belongs to `lotus-core`
2. performance methodology authority, which belongs to `lotus-performance`
3. risk methodology authority, which belongs to `lotus-risk`

## Current posture

- orchestration-heavy service in the canonical front-office stack
- exposed through `report.dev.lotus`
- `POST /reports/portfolios/{portfolio_id}/review` is the RFC-0002 first-class review report
  contract with typed request/response models, client/advisor section separation, readiness states,
  source-backed client profile, key figures, report coverage, advisor briefing, AI-readiness
  metadata, and evidence lineage
- lighter CI than some core domain services, but still under the Lotus lane model
- mixed request-convention surface that must be documented carefully

## Portfolio review contract

The portfolio review endpoint is the highest-value front-office reporting surface in this repo. It
produces a machine-readable meeting pack rather than a cosmetic document:

1. client profile and mandate context are sourced from `lotus-core` where available,
2. performance and contribution are sourced from `lotus-performance`,
3. risk analytics are composed through the report review flow and `lotus-risk`,
4. section readiness and report coverage make missing enterprise-grade content explicit,
5. advisor-only material is separated from client-ready report sections,
6. AI assistance is represented as guarded readiness metadata, not generated advice.

Full guide: [Portfolio Review Report](Portfolio-Review-Report).
