# Repository Engineering Context

This file provides repository-local engineering context for `lotus-report`.

For platform-wide truth, read:

1. `C:\Users\Sandeep\projects\lotus-platform\context\LOTUS-QUICKSTART-CONTEXT.md`
2. `C:\Users\Sandeep\projects\lotus-platform\context\LOTUS-ENGINEERING-CONTEXT.md`
3. `C:\Users\Sandeep\projects\lotus-platform\context\CONTEXT-REFERENCE-MAP.md`

## Repository Role

`lotus-report` is the reporting and aggregation service for Lotus.

It builds reporting-oriented read models and reporting payloads from authoritative upstream services.

## Business And Domain Responsibility

This repository owns:

1. reporting read-model aggregation,
2. report summary and portfolio-review payloads,
3. reporting metadata and download-reference contracts.

It is not the domain authority for core portfolio or analytics truth; it composes them for reporting use.

## Current-State Summary

Current repository posture:

1. `lotus-report` composes summary and review payloads from `lotus-core`, `lotus-performance`, and `lotus-risk`,
2. it is part of the canonical front-office stack and is exposed through `report.dev.lotus`,
3. CI is standardized but still lighter than some core domain services,
4. cross-app orchestration accuracy matters because reporting payloads summarize authoritative upstream state.

## Architecture And Module Map

Primary areas:

1. `src/app/`
   reporting API and aggregation logic.
2. `scripts/`
   migration, OpenAPI, and monetary-float governance.
3. `tests/`
   unit, integration, and e2e validation.
4. `docs/standards/`
   local standards and ownership guidance.

## Runtime And Integration Boundaries

Runtime model:

1. FastAPI reporting service,
2. consumed through `lotus-gateway` and reporting-oriented flows,
3. depends on `lotus-core`, `lotus-performance`, and `lotus-risk`.

Boundary rules:

1. upstream domain truth stays in the authoritative services,
2. this service owns reporting aggregation and reporting contract shape,
3. canonical service identity should be used for cross-app validation,
4. report-ready payloads must remain faithful to upstream evidence.

## Repo-Native Commands

Use these commands as the primary local contract:

1. install
   `make install`
2. fast local gate
   `make check`
3. PR-grade local gate
   `make ci`
4. feature-lane parity
   `make ci-local`
5. Docker build
   `make docker-build`

## Validation And CI Expectations

`lotus-report` uses explicit CI lanes:

1. `Remote Feature Lane`
2. `Pull Request Merge Gate`
3. `Main Releasability Gate`

Important validation expectations:

1. OpenAPI, typecheck, migration smoke, and security audit are active,
2. split unit, integration, e2e, and coverage validation are part of the merge gate,
3. reporting orchestration changes should be evaluated for cross-app impact.

## Standards And RFCs That Govern This Repository

Most relevant current governance:

1. `C:\Users\Sandeep\projects\lotus-platform\rfcs\RFC-0050-core-data-analytics-and-reporting-service-boundaries.md`
2. `C:\Users\Sandeep\projects\lotus-platform\rfcs\RFC-0067-centralized-api-vocabulary-inventory-and-openapi-documentation-governance.md`
3. `C:\Users\Sandeep\projects\lotus-platform\rfcs\RFC-0071-centralized-environment-scoped-service-addressing-and-ingress-governance.md`
4. `C:\Users\Sandeep\projects\lotus-platform\rfcs\RFC-0072-platform-wide-multi-lane-ci-validation-and-release-governance.md`
5. `C:\Users\Sandeep\projects\lotus-platform\rfcs\RFC-0073-lotus-ecosystem-engineering-context-and-agent-guidance-system.md`
6. `docs/standards/data-model-ownership.md`

## Known Constraints And Implementation Notes

1. reporting quality depends on upstream contract fidelity; drift here can misstate portfolio or analytics reality,
2. the service is orchestration-heavy, so naming and payload clarity matter,
3. canonical `report.dev.lotus` identity should be used for real cross-app validation,
4. reporting work should update both code and orchestration docs when contracts change materially.

## Context Maintenance Rule

Update this document when:

1. report payload ownership or major orchestration scope changes,
2. repo-native commands or CI expectations change,
3. upstream dependency posture changes materially,
4. canonical runtime identity or front-office integration role changes,
5. current-state rollout posture changes.

## Cross-Links

1. `C:\Users\Sandeep\projects\lotus-platform\context\LOTUS-QUICKSTART-CONTEXT.md`
2. `C:\Users\Sandeep\projects\lotus-platform\context\LOTUS-ENGINEERING-CONTEXT.md`
3. `C:\Users\Sandeep\projects\lotus-platform\context\CONTEXT-REFERENCE-MAP.md`
4. `C:\Users\Sandeep\projects\lotus-platform\context\Repository-Engineering-Context-Contract.md`
