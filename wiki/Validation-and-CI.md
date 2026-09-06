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
- `python scripts/check_branch_protection_policy.py --offline`
  branch-protection policy table is complete and self-consistent; needs no token and runs in
  every governed lane through the unit gate
- `python scripts/check_branch_protection_policy.py`
  live `main` protection matches `quality/branch_protection_policy.v1.json` field by field.
  Needs a token carrying `administration: read` and **fails closed without one** - a missing or
  under-scoped token is reported as an error, never as a pass
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
- branch-protection policy gate
  offline half proves the policy table is well-formed and blocks in every governed lane; the
  live half runs as its own scheduled `branch-protection` job in
  `main-gate-coverage-audit.yml` and fails in **both** drift directions, so protection that is
  weakened *or* strengthened away from the recorded policy is surfaced rather than absorbed

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
callers that already own an isolated database. Never point `make ci` at a database used by
running services: the lane marks the caller's isolation promise via
`REPORT_JOB_LEDGER_DATABASE_IS_ISOLATED`, so the integration-test session trusts the given
database. Bare `pytest tests/integration` (or `make test-integration`) instead provisions its own
ephemeral `lotus_report_ci_<token>` database, so it is safe alongside the running local stack.

## Branch-protection policy gate

`quality/branch_protection_policy.v1.json` records the protection `main` is *supposed* to carry;
the checker compares it against what GitHub actually reports. Two halves, deliberately split:

- **Offline** (`--offline`) validates the table itself and runs inside the unit gate, so it blocks
  every merge. No token, no network.
- **Live** compares the table against the GitHub API. It runs on a schedule as its **own**
  `branch-protection` job rather than a step inside the coverage-audit job, because a shared job's
  timeout would cancel the protection evidence at exactly the moment it is most useful.

**Operator requirements, in order of how often they bite:**

1. The live comparison needs a token carrying **`administration: read`**. `github.token` does not
   have it. Without it the job **fails closed** - that is the designed behaviour, not a
   misconfiguration to route around, and the fix is to provision the scope rather than to relax
   the check.
2. The live half is **not yet a required merge context**. A drift finding is therefore visible but
   non-blocking; treat a red `branch-protection` job as an action item, not as noise.
3. Drift is reported, **never auto-corrected**. Protection changes are operator-gated by design, so
   the gate's output is a remediation command for a human to run.

The checker and its tests are lifted **byte-identically** from the canonical implementation in
`lotus-gateway` and must stay that way, so every adopter inherits one behaviour and upstream fixes
arrive by re-lift. Repository-specific needs belong in the policy table or adopter-side config -
never in the script. Three known canonical gaps are recorded in the table's stated limitations
(`lotus-gateway#740`, `#742`, `#743`); none is closable from the table side.

The gate does not yet assert its own context is required - self-anchoring is deliberately deferred
rather than overlooked, because making it required is itself an operator-gated protection write.

## Contract emphasis

- integration capability query semantics matter because downstream consumers rely on them
- reporting orchestration changes should be reviewed for cross-app impact
- summary/review request-convention compatibility should be documented truthfully when changed
