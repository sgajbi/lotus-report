# Validation and CI

## Lane model

`lotus-report` uses:

1. `Remote Feature Lane`
2. `Pull Request Merge Gate`
3. `Main Releasability Gate`

`Main Releasability Gate` does not run on push. It is dispatched by
`merged-pr-main-releasability.yml` once a pull request merges, against an immutable tag created at
the merge commit, and its first job refuses to continue unless the checked-out revision matches the
`expected_sha` it was dispatched with.

That is deliberate. Automated merges run under `secrets.LOTUS_AUTOMERGE_TOKEN`; had they run under
`github.token`, GitHub would not treat the merge push as a trigger and the gate would silently not
run at all. A dispatcher that fails is visible; a suppressed push trigger is not. Gate concurrency
is also keyed per commit rather than per branch, so a later merge cannot cancel an earlier commit's
in-flight gate and leave it with a run that is neither pass nor fail.

The dispatch tag is consumed, not kept. At the end of every governed run, the
`reclaim-dispatch-tag` job deletes the exact `main-releasability-<sha>` tag the run validated,
after proving the tag still points at that SHA. Cleanup can never change the gate's verdict
(`continue-on-error` at job and step level; any guard failure warns and retains the tag).

Tag absence is therefore not evidence in either direction: a consumed run and a dispatcher that
never fired both leave no tag behind. The only way to distinguish them is the run lookup below.

**Auditing a merge:** use `gh run list --commit <full-sha>`. Listing by `--branch main` misses the
run, because the dispatch ref is a tag rather than `main` - and tag presence is not durable
evidence, because consumed tags are reclaimed.

## Code-health gates

`make code-health-gates` runs four equality-banked fitness functions in both governed lanes:
`complexity-gate` (max cyclomatic complexity and rank-D+ function count),
`source-size-gate` (largest module), `dead-code-gate` (Vulture over a proven-non-empty tree,
zero findings and no whitelist), and `dependency-hygiene-gate` (deptry). Thresholds equal
today's measurement exactly - `tests/unit/test_code_health_gates.py` asserts the equality in
both directions and proves each gate can fail, so a regression blocks and an improvement must
be banked in the same commit.

## Local command mapping

- `make check`
  lint, typecheck, OpenAPI gate, monetary float guard, domain-data-product contract validation,
  idea-evidence intake and materialization contract gates, unit tests.
  Issue #182 put all gate targets into these lanes; issue #187 then measured that CI never runs
  the lanes themselves - `make lint` had quietly chained three of the four gates all along, and
  `domain-product-validate` alone had never executed until it was wired as an explicit workflow
  step. `tests/unit/test_gate_reachability.py` now enforces both directions: gates must be
  reachable from `check`/`ci` *and* executed by **each** governed lane - `pr-merge-gate.yml` and
  `main-releasability.yml` independently, directly or through `$(MAKE)` chains. A gate missing
  from one lane fails the test naming that lane.
- `make ci`
  automation-oriented migration, integration, e2e, coverage, and security proof against a
  caller-owned isolated PostgreSQL database
- `make migration-upgrade-smoke`
  isolated real-PostgreSQL upgrade proof from `report-status-event-pre-contract-v0` to
  `report-ledger-v1`, including legacy-row preservation and deterministic rerun
- `make ci-local`
  preferred workstation gate; creates one temporary PostgreSQL database, runs `make ci`, and drops
  only the helper-owned database on success or failure
- `make docker-build`
  container build validation
- report-work lifecycle proof
  `python -m pytest tests/unit/reporting_jobs/test_report_job_work_queue.py tests/unit/reporting_jobs/test_report_job_worker.py tests/integration/test_report_job_api.py::test_report_job_replay_creates_new_job_and_is_idempotent -q`
  proves bounded explicit and expired-lease retry, one-at-a-time claim-before-execute under parallel
  workers, coherent source-job failure, and replay eligibility without requiring PostgreSQL.
- `scripts/rfc_0104_slice4_live_evidence.py`
  PostgreSQL-backed live evidence for internal RFC-0104 batch dispatch primitives; requires
  `REPORT_JOB_LEDGER_DATABASE_URL`
- canonical Docker front-office proof
  run from `lotus-workbench` with
  `scripts/live/Start-LotusFrontOfficeCanonical.ps1 -CleanCoreState -BuildImages -RunValidation`
  when a change must be proven against the production-shaped local stack.
- Docker runtime readiness proof
  `docker compose up -d --build lotus-report lotus-report-job-worker lotus-report-batch-worker lotus-report-batch-scheduler`
  against both a fresh volume and a preserved supported prior-schema volume, then verify
  `http://report.dev.lotus/health/ready` returns 200 and the PostgreSQL schema includes
  `report_job`, `report_job_work_item`, `report_batch`, `report_input_snapshot`,
  `report_upstream_call`, and the typed status-event contract. A destructive volume reset is not
  upgrade evidence.

## What the gates protect

- `make check`
  fast proof that lint, typing, OpenAPI quality, and unit behavior still match repo truth
- `make ci`
  PR-grade proof that integration, e2e, coverage, fresh/current schema, supported prior-schema
  upgrade, and security posture still hold
- combined coverage gate
  enforces the repo's 97% coverage floor across unit, integration, and e2e packs

## Safe local database lifecycle

Keep the canonical Report API, report-job worker, batch worker, and scheduler on their normal
`lotus_report` database and
run local PR-grade proof through the isolation helper:

```powershell
$env:REPORT_JOB_LEDGER_DATABASE_URL="postgresql://lotus_report:lotus_report@localhost:5439/lotus_report"
make ci-local
```

The configured role must be allowed to create and drop databases. The helper derives a bounded,
unique database name, never runs stateful test suites against the source database, suppresses DSN
output, terminates only connections to the database it owns, and drops that exact database in a
guaranteed cleanup path. `make ci` remains the automation primitive for GitHub Actions and other
callers that already own an isolated database.

## Contract emphasis

- integration capability query semantics matter because downstream consumers rely on them
- reporting orchestration changes should be reviewed for cross-app impact
- summary/review request-convention compatibility should be documented truthfully when changed
