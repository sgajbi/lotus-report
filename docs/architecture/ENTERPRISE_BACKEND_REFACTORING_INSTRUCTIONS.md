# Enterprise Backend Refactoring Instructions

Use this file as the detailed refactoring instruction pack for any Lotus backend application.

The Codex Goal prompt should name the target application and instruct the agent to follow this file fully.

---

## 0. Scope and Preconditions

Before editing, confirm:

- Repo governance for the target app (AGENTS.md, repository engineering context, and local conventions).
- Target stack (language/runtime, web framework, persistence, auth mechanisms, deployment model).
- Branching rule:
  - For `lotus-risk`, use branch work anchored to `feat` if that repo policy requires it.
  - For all other Lotus backend repos in active mode, branch from and target `main` by default.
- Baseline evidence to compare against:
  - build/test status,
  - quality baseline reports,
  - endpoint inventory and API trust/contract artifacts.
- Any durable-truth area affected (RFC, API contract, wiki, context, runbooks): plan stranded-truth reconciliation before final closure.

---

## 1. Mission

Refactor the target Lotus backend application into a modular, reusable, maintainable, performant, secure, observable, enterprise-grade, production-ready, bank-buyable application.

The application must align with:

- Lotus platform governance
- API governance
- data mesh standards
- private banking engineering practices
- production support expectations
- secure software delivery practices
- implementation-backed documentation expectations

The goal is not cosmetic cleanup. The goal is to make the application easier to understand, safer to change, easier to operate, easier to test, easier to explain, and suitable for enterprise adoption.

Primary acceptance target: every refactor slice must improve these three dimensions together:

- reliability and risk posture,
- developer maintainability and execution speed,
- production supportability and incident readiness.

---

## 2. Working Rules

Work on a feature branch.

Use small, meaningful commits. Target roughly 25–50 well-scoped commits.

Preserve normal commit history because the final PR will use a non-squash merge strategy.

Each commit should have a clear purpose and should keep the application buildable and testable as much as possible.

Avoid large mechanical rewrites unless they create clear architectural value.

Preserve existing behavior unless intentionally changing it.

When behavior changes, document the reason, update tests, and explain the impact in the PR.

Keep branch policy strict:

- do not push directly to protected branches,
- isolate one repository per Codex run,
- do not mix non-orthogonal refactors in one PR unless clearly related.

Before closing, include branch name, target branch, and validation evidence in PR metadata.

---

## 3. Refactoring Focus

Improve the application across these areas:

- remove dead code and unused paths
- split monolithic files into clear modules
- reduce duplication across routers, controllers, services, DTOs, mappers, validators, middleware, clients, repositories, and tests
- improve domain modeling and private banking vocabulary
- improve API design, versioning, routing, pagination, filtering, sorting, and error handling
- complete OpenAPI with descriptions, examples, tags, operation IDs, request models, response models, and error models
- keep business logic out of routers, controllers, middleware, infrastructure, and persistence layers
- improve service boundaries, dependency flow, orchestration boundaries, and separation of concerns
- strengthen validation, idempotency, correlation IDs, auditability, lineage, and traceability
- improve structured logging, metrics, tracing, health checks, readiness checks, and operational diagnostics
- optimize latency, batching, pagination, caching, connection pooling, timeout handling, retry behavior, and downstream access patterns
- harden security, authentication, authorization, sensitive-data handling, secrets handling, configuration, CORS, headers, and API abuse protection
- improve resilience with timeouts, retries, circuit-breaker-style boundaries, graceful degradation, and consistent downstream error mapping
- improve tests with meaningful unit, integration, contract, API, middleware, security, regression, and end-to-end coverage
- update README, wiki, RFC, architecture diagrams, API catalog, operational runbooks, and supported-features material
- enforce deterministic behavior for breaking changes through migration/deprecation strategies
- improve data ownership, schema migration safety, and rollback strategy
- standardize dependency lifecycle, lockfile hygiene, and reproducible install strategy
- strengthen idempotency semantics and replay safety for state-changing flows

---

## 4. Architecture Principles

Use clear layered architecture.

Preferred dependency flow:

```text
api / routers / controllers
    -> application / use_cases / services
        -> domain / models / value_objects / policies
        -> ports / interfaces
            <- infrastructure / adapters / clients / repositories
```

Rules:

- routers/controllers call application services or use cases only
- routers/controllers must not call repositories, database clients, HTTP clients, Kafka clients, Redis clients, file clients, or downstream adapters directly
- middleware must stay thin, reusable, and free of business logic
- domain and application logic must not depend on FastAPI, framework objects, infrastructure clients, persistence models, or transport DTOs
- infrastructure must sit behind ports/adapters
- API DTOs must not leak into domain logic
- persistence models must not leak into API responses or domain logic
- downstream errors must map to consistent platform errors
- errors should follow RFC 7807/problem-details style where applicable
- every request must support or propagate correlation ID
- relevant mutations must be auditable
- idempotent operations must define explicit idempotency behavior
- logs must be structured and must not leak sensitive data
- enforce boundary violations through automated checks (import-linter, dependency maps, package boundaries)
- keep orchestrator code out of domain entities/value objects
- isolate config/feature flag readers from business logic for deterministic behavior

---

## 5. API Governance

Improve APIs so they are consistent, explainable, and enterprise-ready.

Every endpoint should have:

- clear route naming
- consistent versioning
- summary
- description
- tags
- operation ID
- request model
- response model
- useful request and response examples
- standard error responses
- documented validation behavior
- documented correlation ID behavior
- documented idempotency behavior where relevant
- documented paging stability and sort ordering
- documented backward-compatibility policy and deprecation notices
- documented authz and audit impact

Standardize:

- pagination
- filtering
- sorting
- status codes
- versioning
- deprecation strategy
- internal versus public endpoints
- health, readiness, liveness, and metrics endpoints
- endpoint certification expectations against repository ledger/wiki truth where it exists
- request strictness and unsupported-query behavior

For public endpoints, keep backward compatibility rules explicit and codify migration windows.

---

## 6. Observability and Operations

Improve operational readiness.

The application should support:

- structured JSON logging
- correlation ID in logs and responses
- safe logging without secrets, tokens, personal data, or client-sensitive data
- request metrics
- latency metrics
- downstream call metrics
- error metrics
- saturation metrics for queues, pools, thread/execution workers, and storage connectors
- business-outcome metrics for critical financial workflows
- health checks
- readiness checks
- liveness checks
- tracing hooks where practical
- clear operational diagnostics
- meaningful startup and shutdown behavior
- runbook documentation
- explicit SLIs/SLOs and alert thresholds
- dashboards and alerting rules bound to implemented metrics
- startup dependency graphs and dependency timeout behavior

Operational evidence should include failure-path expectations and recovery behavior, not only success-path monitoring.

---

## 7. Security

Harden the application.

Check and improve:

- authentication boundaries
- authorization boundaries
- secrets handling
- environment-based configuration
- safe defaults
- sensitive-data masking
- secure headers where applicable
- CORS policy
- input validation
- dependency vulnerabilities
- insecure Python patterns
- downstream error leakage
- logs for sensitive-data exposure
- test coverage for negative/security cases
- secret scanning and credential leakage prevention in CI
- container and image trust posture
- identity and access control model clarity

Do not expose tokens, secrets, credentials, internal stack traces, or sensitive client data.

Required security posture checks:

- threat-model for trust boundaries and abuse cases
- RBAC/ABAC enforcement validation for protected flows
- least-privilege service credentials
- data-classification review for PII-like and market-sensitive identifiers
- encryption requirements for transit and rest where applicable
- security dependency and supply-chain scans with fail-on-regression policy

---

## 8. Testing Expectations

Improve tests so they protect behavior, architecture, and production readiness.

Add or strengthen:

- unit tests for domain logic and application services
- integration tests for infrastructure/adapters where practical
- contract/API tests
- router/controller tests
- middleware tests
- error-handling tests
- security tests
- regression tests for existing supported features
- health/readiness tests
- correlation ID propagation tests
- RFC 7807/problem-details tests
- downstream failure tests
- sensitive-data masking tests
- idempotency tests where relevant
- performance smoke tests where practical
- concurrency and retry-safety tests for state changes
- migration/recovery tests where persistence behavior is changed
- chaos-style dependency failure tests for key upstream/downstream paths
- contract compatibility tests for API schema drift

Tests should be meaningful, not just coverage fillers.

For every behavior-sensitive change, include positive, negative, and backward-compatibility regression coverage.

---

## 9. Quality Gates and Measurement

Add measurable quality gates so refactoring progress is tangible in CI.

Before major refactoring, create a baseline report covering:

- code size
- module/package count
- largest files
- largest functions
- complexity
- maintainability
- lint issues
- formatting issues
- type-checking issues
- test count
- line coverage
- branch coverage
- dead-code candidates
- dependency issues
- security findings
- dependency vulnerability findings
- OpenAPI gaps
- architecture boundary violations
- router/controller complexity
- middleware complexity
- documentation gaps
- observability gaps
- policy enforcement gaps
- resilience gaps
- backward-compatibility break risks

Create a before/after scorecard and update it during the refactor.

Recommended files:

- `pyproject.toml`
- CI quality workflow
- `.importlinter`
- `.spectral.yaml`
- `quality/baseline_report.md`
- `quality/refactor_health_report.md`
- `quality/quality_scorecard.md`
- `quality/architecture_rules.md`
- `quality/api_governance_rules.md`
- `quality/ci_quality_gates.md`
- `quality/enterprise_readiness_checklist.md`
- `quality/ops_slo_contract.md`

Recommended tools where applicable:

- `ruff` for linting and formatting
- `mypy` or `pyright` for type checking
- `pytest`, `pytest-cov`, and `coverage.py` for tests and coverage
- `radon` or `xenon` for complexity and maintainability
- `vulture` for dead code
- `deptry` for dependency hygiene
- `bandit` for security scanning
- `pip-audit` for dependency vulnerability scanning
- `spectral` for OpenAPI governance
- `import-linter` for architecture boundaries
- `interrogate` for docstring coverage
- optional: `schemathesis` for OpenAPI/property-based API testing
- optional: `pytest-benchmark` for performance checks
- optional: `locust` or `k6` for load/performance testing
- optional: `grype`, `trivy`, or `snyk` for supply-chain scanning
- optional: `semgrep` for policy/rule pattern enforcement

Use progressive CI gating:

1. baseline/report-only checks
2. fail only on new regressions
3. enforce agreed thresholds
4. enforce strict enterprise-readiness gates

Each gate should include deterministic pass criteria in `quality/ci_quality_gates.md`.

---

## 10. Documentation Expectations

Treat documentation as part of the product.

Update or create:

- `README.md`
- `docs/architecture.md`
- `docs/api-governance.md`
- `docs/observability.md`
- `docs/security.md`
- `docs/operations-runbook.md`
- `docs/supported-features.md`
- `docs/configuration.md`
- `docs/local-development.md`
- `docs/data-classification.md` (or equivalent)
- `docs/disaster-recovery.md`
- RFCs where architectural decisions changed
- wiki-ready diagrams and flow descriptions
- `wiki/Endpoint-Certification.md` and related wiki operational pages when truth changes
- `REPOSITORY-ENGINEERING-CONTEXT.md` when repository responsibilities change
- `AGENTS.md` only when operating contract changes; keep synchronized with platform copy

Documentation should support:

- developers
- business users
- operations
- sales
- client demos
- client pitches
- future maintainers
- production support
- audit and compliance reviewers

Documentation must be implementation-backed. Do not document capabilities that the code does not support.

If durable truth changed (API, context, wiki, RFC, deployment/runbook, supported-features), run:

1. `git fetch origin --prune`
2. `git branch -r --no-merged origin/main`
3. `lotus-platform/automation/Sync-RepoWikis.ps1 -CheckOnly -Repository <repo-name>`
4. stranded-truth review as required by AGENTS

---

## 11. Suggested Commit Sequence

Use this sequence as guidance, adapting to the repository reality:

1. add baseline quality tooling and report
2. add CI quality workflow in report-only mode
3. add architecture boundary rules
4. add OpenAPI governance rules
5. add controller/router thinness checks
6. add observability and correlation ID checks
7. add security and dependency scanning
8. split large modules into coherent packages
9. extract application services/use cases
10. extract or refine domain models and value objects
11. move infrastructure concerns behind ports/adapters
12. standardize errors and problem-details responses
13. standardize correlation ID propagation
14. standardize idempotency behavior where relevant
15. standardize audit and lineage behavior
16. improve validation and request models
17. improve downstream client boundaries
18. improve retry, timeout, and error mapping behavior
19. improve logging and metrics
20. improve health and readiness checks
21. improve OpenAPI descriptions and examples
22. add or strengthen unit tests
23. add or strengthen integration tests
24. add or strengthen API/contract tests
25. add or strengthen middleware tests
26. add or strengthen security tests
27. add or strengthen performance smoke tests
28. remove dead code and unused dependencies
29. reduce duplication
30. update README, wiki, RFCs, and runbooks
31. tighten CI from report-only to regression-blocking
32. tighten CI to enterprise-readiness thresholds
33. produce final before/after refactor health report
34. validate governance/docs truth synchronization checks
35. validate run/rollback readiness for production-facing behavior changes

---

## 12. Final PR Requirements

The final PR must include:

- summary of refactoring approach
- before/after quality scorecard
- major architectural improvements
- API improvements
- testing improvements
- security improvements
- observability improvements
- documentation improvements
- known limitations
- follow-up backlog
- migration notes if behavior or configuration changed
- explicit quality gates executed and results
- enterprise readiness evidence references
- rollout and rollback guidance
- references to changed API, mesh, and certification artifacts

---

## 13. Definition of Done

The refactor is complete only when:

- CI passes
- scorecard proves measurable improvement
- architecture boundaries are enforced
- OpenAPI quality is improved
- tests protect important behavior
- security and dependency checks pass
- observability behavior is documented and tested
- documentation is implementation-backed
- application behavior is preserved unless changes are explicitly documented
- final PR explains what changed, why it changed, what improved, what risks remain, and what should follow next
- quality scorecards do not regress on critical governance, resilience, and security checks
- API, mesh, and operational contract truth is synchronized with repo-local source docs
- rollout evidence is reproducible from deterministic commands

---

## 14. Codex Goal Exit Checklist

- confirm the target branch policy was followed for the repository,
- confirm each mandatory section above was attempted and evidence attached,
- confirm no accidental broad cross-repo scope changes,
- confirm no unresolved `TODO`/`FIXME` without owners and dates,
- confirm reproducible validation commands are included in PR description.
