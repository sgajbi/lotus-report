# Codebase Review Ledger

This ledger records governed review findings for `lotus-report`.

Tracking model:

1. historical review evidence remains in this ledger for audit and closure context,
2. active validated backlog findings live in GitHub issues, not local-only ledger rows,
3. the current enterprise refactor discovery ledger is
   [#109](https://github.com/sgajbi/lotus-report/issues/109),
4. new local ledger rows for active work must link the corresponding GitHub issue and include the
   focused validation evidence used to move that issue to `status/fixed-local`, `status/pr-open`,
   or `status/merged-main`,
5. local review notes without a linked issue are historical evidence only unless an explicit issue
   is created or reused.

## RFC-0105 Gold-Pass Audit

Review id: `RFC-0105-GOLD-PASS-2026-04-29`

Scope: RFC-0105 reporting observability, operations, replay tooling, implementation proof, and
closure posture.

| Slice | Status | Findings | Actions taken | Evidence |
| --- | --- | --- | --- | --- |
| Slice 0 platform scaffold | Signed off | Platform-owned scaffold defaults are documented in the platform RFC and protected by platform tests. No report-local code debt found. | No report change needed. | Platform RFC proof ledger and `tests/unit/test_repository_hygiene_scaffold_contract.py`. |
| Slice 1 observability structure | Signed off | Runtime vocabulary is centralized in `src/app/observability.py`; no duplicate local field registry found in the reviewed RFC-0105 surface. | No code change needed. | `tests/unit/test_observability.py`; RFC proof ledger. |
| Slice 2 trace and structured logging | Signed off | Trace/correlation propagation is source-backed across report/render/archive. Sensitive log proof remains bounded to captured evidence, not a universal data-loss-prevention claim. | No code change needed. | Slice 2 closure evidence and RFC proof ledger. |
| Slice 3 metrics, dashboards, alerts, SLA contracts | Signed off | Metric vocabulary is bounded in `src/app/reporting_metrics.py`; platform dashboard and alert contracts intentionally reference implemented metrics only. | No code change needed. | Platform reporting observability contract tests and report observability tests. |
| Slice 4 diagnostics | Signed off | Diagnostics API is support-safe and intentionally one-job scoped. Broader lookup surfaces remain planned. | No code change needed. | `tests/integration/test_report_job_api.py`; OpenAPI quality gate. |
| Slice 5 rerender | Hardened | Live proof exposed a render-contract mismatch that was already fixed. Audit found live proof captures kept only the latest render/archive request and response, which weakened per-operation inspection. | Added numbered render/archive request and response captures while preserving latest stable filenames. | `scripts/rfc_0102_proof_app.py`; live proof harness. |
| Slice 6 regenerate | Hardened | Regenerate behavior is implementation-backed and separate from rerender. Same capture-overwrite evidence weakness applied to regenerate proof inspection. | Same capture sequencing fix records regenerate render/archive calls independently. | `scripts/rfc_0102_proof_app.py`; live proof harness. |
| Slice 7 replay | Hardened | Failed job and batch-item replay have explicit eligibility and idempotency tests. Same capture-overwrite evidence weakness applied to replay-generated render/archive calls. | Same capture sequencing fix records replay render/archive calls independently. | `scripts/rfc_0102_proof_app.py`; replay unit/integration suites. |
| Slice 8 attention scanning | Signed off | Attention events are source-backed, bounded, and support-safe. Broader productized dashboards remain out of RFC-0105 scope. | No code change needed. | `tests/unit/reporting_operations/test_attention.py`; attention API integration test. |
| Slice 9 live proof | Hardened | Proof is end-to-end and meaningful, but evidence inspection was improved by preserving every render/archive call instead of overwriting captures. | Added per-call numbered evidence captures for render and archive clients. | `scripts/rfc_0105_live_evidence.py`; live evidence pack rerun required after this audit change. |
| Closure docs and supported features | Signed off | Final RFC clearly bounds first-wave completion versus RFC-0106/RFC-0107 residual scope. Audit requested an explicit debt-removed statement. | Platform RFC final assessment updated to call out slice-by-slice audit and debt removed. | Platform RFC and closure governance test. |

Follow-up:

1. Rerun the RFC-0105 live evidence harness after the capture sequencing change and record the new
   evidence directory in the platform RFC.
2. Keep the known published-wiki drift as a pre-existing publication issue unless this branch takes
   ownership of wiki publication cleanup.

## Enterprise Backend Refactor Issue Closure

| Issue | Status | Finding | Actions taken | Evidence | Docs/wiki/context decision |
| --- | --- | --- | --- | --- | --- |
| [#117](https://github.com/sgajbi/lotus-report/issues/117) | Hardened | Enterprise audit middleware enforced write payload limits only when `Content-Length` declared an oversized body, and accepted missing or malformed lengths. | Added malformed `Content-Length` rejection, actual write-body stream counting, oversized streamed/missing-length rejection, underdeclared body rejection, and downstream body replay for valid requests. | Focused enterprise-readiness middleware tests passed; `make check` passed after the slice. | Updated `wiki/Security-and-Governance.md` because direct-service HTTP boundary posture changed. Wiki publication required after merge. |
| [#128](https://github.com/sgajbi/lotus-report/issues/128) | Hardened | Operator-facing wiki examples omitted caller-context, correlation/trace, or idempotency details required by report-job and batch routers. | Added a reusable `report-operator-headers.curl` caller-context config to API and operations wiki pages; routed report-job, support-safe read, cancel, rerender, regenerate, replay, batch materialization, batch control, batch item replay, and run-once examples through it; added docs contract tests for caller-context config and idempotency-key examples. | Focused operator-runbook docs test passed; `make check` passed after the slice. Wiki audit structural findings in changed pages were fixed; residual audit findings are bare-URL reports in executable curl examples. | Updated `wiki/API-Surface.md` and `wiki/Operations-Runbook.md` because operator command examples are user-facing support truth. Wiki publication required after merge. |
| [#132](https://github.com/sgajbi/lotus-report/issues/132) | Hardened | The review playbook and repo context did not tell agents to search/reuse GitHub issues, update the issue-discovery ledger, or keep active backlog state out of local-only docs. | Added GitHub issue-discovery workflow steps, required issue fields, duplicate-search proof, #109 ledger comment format, and active-backlog versus historical-ledger guidance; updated repo context and wiki navigation; added docs regression coverage for these controls. | Focused docs guidance test passed; `make check` passed after the slice. Wiki audit direct findings in touched pages were fixed; residual audit findings are legacy bare-URL reports in executable curl/example pages outside this issue's scope. | Updated `wiki/Development-Workflow.md` because review-to-issue workflow is agent/operator-facing delivery truth; normalized `_Sidebar.md` title and `Security-and-Governance.md` wording found during wiki audit. Wiki publication required after merge. |
| [#123](https://github.com/sgajbi/lotus-report/issues/123) | Hardened | Feature, PR, and main workflows duplicated raw pytest/coverage commands even though Makefile owns the repository-native test and coverage contract. | Added `test-suite-coverage` and `coverage-gate` Make targets; updated Feature Lane, PR Merge Gate, and Main Releasability workflows to consume Make targets; added workflow contract tests blocking raw pytest/coverage command drift. | Focused workflow contract tests passed; `make test-suite-coverage TEST_SUITE=unit TEST_PATH=tests/unit` passed; `make check` passed after the slice. | No wiki change: existing wiki command mapping (`make check`, `make ci`, coverage floor) remains true; workflows now align to that documented command contract. |
| [#122](https://github.com/sgajbi/lotus-report/issues/122) | Hardened | `make security-audit` carried raw inline `pip-audit --ignore-vuln` flags without owner, expiry, linked issue, or deterministic expiration enforcement. | Added governed dependency vulnerability exception records; added a security-audit runner that validates non-expired, issue-linked exceptions before invoking `pip-audit`; updated Makefile routing and tests to reject raw ignore drift. | Focused dependency-exception tests passed; `make security-audit` passed; `make check` passed after the slice. | Updated `docs/standards/dependency-vulnerability-exceptions.md` and `wiki/Security-and-Governance.md` because security exception governance is operator/reviewer-facing truth. Wiki publication required after merge. |
| [#112](https://github.com/sgajbi/lotus-report/issues/112) | Hardened | Batch-item failed-work replay did not emit the documented bounded replay operation metric, leaving operators with job-level replay telemetry but no batch-item replay pressure signal. | Reused existing `operation="replay_command"` metric vocabulary for accepted/idempotent batch-item replay and rejected replay attempts; added integration assertions for accepted, conflict, missing-key, and missing-item metric labels without forbidden identifiers. | Focused batch replay API tests passed; `make check` passed after the slice. | No wiki/docs change: the existing operations docs already describe failed-work replay under `lotus_report_operations_total{operation="replay_command"}` and the implementation now matches that published contract. |
| [#116](https://github.com/sgajbi/lotus-report/issues/116) | Hardened | `ReportingReadService` and lineage capture raised or classified FastAPI `HTTPException` inside application behavior, coupling report orchestration to HTTP delivery. | Added typed reporting application errors; moved `/reports` HTTP translation into the router; updated lineage failure classification to use application failure categories; updated service tests to assert application errors and API tests to assert HTTP mapping. | `make check` passed; focused service/lineage/API tests passed; boundary scan found no FastAPI imports or `HTTPException` assertions in the service and lineage paths covered by #116. | No wiki change: external API behavior is preserved; this is an internal architecture-boundary refactor. Monetary-float allowlist was refreshed only for line-number drift caused by this service refactor, with prior review dates preserved. |
