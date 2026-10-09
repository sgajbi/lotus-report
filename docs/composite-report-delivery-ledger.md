# Composite Report Delivery Ledger

Issue: [Report #417](https://github.com/sgajbi/lotus-report/issues/417), under
[Platform #923](https://github.com/sgajbi/lotus-platform/issues/923).
Feature: `feature/composite-performance`. Owner: chat `01a11def-da27-72c3-88e4-948bdff1b30b`.

## Active Slice

Branch `feat/composite-excel-417` starts from freshly fetched
`origin/main` `f62053d91a3a44d3c7dcacf14982ffce870febb4`.
Owned checkout: `C:/Users/Sandeep/projects/worktrees/lotus-report-composite-417`.
Primary Report checkout remains read-only. Initial primary state: clean `main`,
one registered worktree, no open PRs. Initial stranded-truth reconciliation:
`git fetch origin --prune` and `git branch -r --no-merged origin/main` exited 0;
no unmerged remote branches required classification.

Write scope: Report catalogue/schema, exact source capture, immutable dataset,
existing lifecycle/Excel handoff, owning tests, documentation/wiki/context.
No Performance, Manage, Render, Archive or Platform implementation writes.
No canonical Docker stack operation. New capability modules belong under
`src/app/composite_reporting/`; tests mirror this bounded package.
No deployment split or financial engine is introduced.

## Contract Intake

- Report composition uses `report_ordering_catalogue`, `reporting_jobs`,
  `reporting_lineage`, `reporting_identity` and `reporting_render` foundations.
  Existing native gates: `make lint`, `make typecheck`, `make code-health-gates`,
  `make openapi-gate`, `make test-unit`, and isolated PostgreSQL integration.
- Performance `POST /composites/twr` accepts an explicit ordered vector of
  immutable materialization IDs. Its `selection_manifest` retains exact window,
  definition/membership/universe digest, source cut, method binding, receipt,
  engine version and calculation fingerprint. Its qualification is
  `EXPLICIT_RETAINED_CALCULATED_REPLAY`; it grants no official/freeze authority.
  The current producer limit is 120 windows. Larger selections must refuse,
  never truncate. Materialization inspection is revision-paged and retains
  included/excluded/waiting/blocked identities. Consumer support must prove
  actual wire behavior rather than infer it from schema names.
  Inspection provenance: Performance primary HEAD
  `82fb0ac182eadc18ade2cbeff5c45ae72ca21940` (PR #630), not merged main.
  Exact-vector support is a named PR #630 dependency until protected main proof.
- Render `src/app/core/settings.py` currently declares only PDF. Template
  registry validates format support; no Excel renderer was found. Source-backed
  supplier intake sent to coordinating Root on 9 October 2026 Singapore.
- Archive's existing generated-report enum excludes composite review and its
  portfolio identity is mandatory. Archive #176 owns the explicit composite
  scope and candidate identity extension; Render #338 owns the Excel engine.
  Neither supplier is assumed accepted before protected-main proof.

## Preserved Acceptance

RPT-01 is the first executable milestone; RPT-01–12 and all #417 acceptance
remain open. Admission/controlled fixture proof alone does not establish
real Performance/Render/Archive, workbook parsing,
retained correction/rerender, official activation, GIPS, recipient delivery,
or enterprise capacity. Manage #714, Performance #540/#609/#610/#627,
Gateway #820 and programme authority/approval contracts remain named dependencies.

Required numerical examples: assets 100/300 and returns +10%/-2% yield the
Performance-owned +1%; +1%/+2% monthly returns yield +3.02%. Report echoes
these facts and does not calculate them. Negative/zero/null values, missing
month/member, changed authority, identity preservation, cross-tenant refusal,
formula injection and partition limits need meaningful consumer proof.

Quality signals to preserve: finite Decimal admission, exact source/tenant
identity, fail-closed source status, immutable hashes, accepted template axes,
bounded function size/complexity, OpenAPI vocabulary and warning-clean tests.
Supplier absence remains a visible blocker; no gate waiver is authorized.

## Evidence And Next Action

Status: RUNNING. PR/head: not yet created. Exact pinned capture and semantic
contract are implemented locally. Registered SQLite and actual PostgreSQL
order/worker/capture/retrieval proofs pass with controlled Performance. A separate
process reads the exact retained PostgreSQL snapshot. Missing-month refusal
persists failed evidence without a financial revision; two-tenant isolation passes.

Actual PostgreSQL native command `3d5d69` exited 0: three cases passed. The third
case supplies a test-only candidate XLSX family definition to normal submission
validation, then exercises the actual worker/composer into a controlled Render
503 boundary. It emits the persisted composite dataset, revision and exclusive
Archive #176 metadata; no workbook or archived completion is asserted.
Its external packet is `report417-candidate-pg-producer`, with per-file source
manifest and artifact hashes. The production catalogue remains JSON-only.
No canonical product stack was changed; PostgreSQL proof uses an owned ephemeral
test container and the repository's isolated-database fixture.

The first candidate test refused the custody digest because Report stores series
and source digests as bare hex but factual digest as `sha256:` text. The adapter
now verifies all against the persisted revision binding before normalizing the
three Archive identity digests to bare hex. Rejection tests cover foreign job,
missing digest, changed source digest and changed source vector.

Independent supplier component proof now exists: Render #338 consumed the actual
Report PostgreSQL producer package through its registered API and engine, and
Archive #176 consumed that exact workbook through its registered PostgreSQL and
filesystem path. The workbook SHA is
`e02ddad3d6be95c7628b3a25959c969ea76c2f852058c3287b81621ed8116d0e`;
Render reconciled 154 canonical and 154 display cells. The first captured Render
response had null archive state/request/document fields and was not completion
evidence. A subsequent genuine registered Render engine/handoff response adopted
the actual Archive 201 through explicitly controlled HTTP transport.
Report's registered PostgreSQL worker reproduced the exact original package,
then exercised a controlled lost-response fault. After reopening both adapters,
the durable worker resolved that genuine sender response under the original
render identity and reached archived custody without resubmission or recapture
(`1e935d`, exit 0). Exactly one submit, one owner lookup and one source call;
the retained snapshot was unchanged and a subsequent worker pass claimed no work.
The second artifact SHA is
`a58464d5f45f6bb4432cf8245947a0a71e644dd86e445cd0bc0ddcdf8f8e28e2`.
Qualification remains controlled synthetic Performance, test-only XLSX admission,
development template, `NOT_ATTESTED`, and `live_network_claim=false`. Exact
identity reproduction occurs only in a fresh helper-owned test database.
These captures are not a live production chain or protected-main qualification.

Next: complete Report native gates and source/schema parity, independent actual
Render/Archive consumer proof, original/corrected/rerender retention, and protected
supplier-main qualification before workbook catalogue activation.
Documentation/wiki support claims will remain bounded by actual merged-main
proof. Skill/context promotion decision will be recorded before PR closure.

Local governance evidence: native `make lint typecheck` (`b1c40d`, exit 0),
`make code-health-gates openapi-gate` (`54a80a`, exit 0). Linux dependency closure
is explicitly not evaluable on this Windows host and remains a required CI check.
The initial complexity regression was removed through bounded composite helpers;
the high-complexity count remains 8 and maximum CC remains 28. No ceiling changed.
The existing package-builder float allowance was mechanically moved from line
1920 to 1954; its exact expression, owner, justification and expiry remain intact,
and findings/allowances remain 29. This is not a new financial-float waiver.
Regression subset: 782 passed (`9e9a90`, exit 0), with one existing SQLite default
date-adapter deprecation warning. Six-year retention and schema/example parity:
2 passed (`9c6b71`, exit 0). Representative refusal boundaries: 28 passed
(`f9a8fa`, final pytest exit 0; preceding formatting repaired separately).

The exploratory all-suite single-process run (`e9c64d`, exit 1) is failed evidence:
7 failed, 3128 passed, combined coverage 96.91% below the required 97%.
Six hook failures identified borrowed-environment drift against committed pins;
one readiness failure occurred with suites sharing a process. An owned worktree
environment was installed against `constraints.txt` with the constrained build
backend and passed `pip check`. In that environment the previously failing hook
and isolated e2e cases pass (43 cases, `b38094`, exit 0). Native suite processes
and combined CI coverage remain required; no threshold or exclusion changed.
Additional finite-value, source-identity, semantic refusal and correction tests
cover meaningful changed behavior rather than accepting that failed run.

Owned pinned environment: composite plus hook tests 148 passed; isolated
PostgreSQL retention/custody tests 3 passed (`8b1cfe`, exit 0).
Lint/typecheck/code-health/OpenAPI completed successfully (`4cbf7c`, exit 0).
Actual PostgreSQL migration and schema-upgrade smoke passed (`aeb6c0`, exit 0).
Domain product validation reported one producer and one consumer declaration;
the earlier migration invocation without a DSN failed and was rerun with the
owned test database. Required Linux closure and combined coverage await CI.

The full native unit suite passed: 2518 passed, 1 skipped, three existing
deprecation warnings in 210.19 seconds (`3c5904`; final process status recorded
before commit). Required Linux closure and combined coverage await CI.

Wiki decision: update authored `wiki/Composite-Review.md`, Home, ordering and
sidebar in this slice, then use governed pre-merge parity and post-merge publication.
Repository-local architecture and proof boundaries are recorded in the context.
No central skill/routing change is needed: existing backend, pre-merge and wiki
skills already route this work, and supplier implementations remain separate owners.
