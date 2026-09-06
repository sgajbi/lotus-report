# lotus-report

Governed report composition for Lotus wealth applications.

`lotus-report` assembles authoritative portfolio information, analytics, and reviewed material
into traceable report packages for clients and advisors. It coordinates rendering and archival
through the services that own them — it composes and explains; it never recomputes a number an
authoritative service owns, never decides how a document looks, and never stores the archived
document itself.

## What it enables

- **Advisor-ready portfolio reviews** — a client meeting pack assembled from sourced portfolio,
  performance, and risk truth, with client-safe and advisor-only content strictly separated and
  every section honest about what is sourced, partial, or unavailable.
- **Traceable client documents** — every report is reproducible and explainable after the fact:
  an immutable evidence snapshot, append-only source lineage, a canonical revision identity, and
  a custody chain from render through archive.
- **Governed report operations** — durable, idempotent report jobs with operator-safe search,
  diagnostics, recovery paths, recurring schedules, and batch runs that converge instead of
  duplicating client documents.
- **Reviewed-content inclusion under authority** — accepted advisor briefs, reviewed proposal
  narratives, and reviewed opportunity evidence enter reports only through explicit acceptance
  contracts; nothing is rewritten, summarized, or invented on the way in.

## Report families

| Family | What the reader gets | Availability |
| --- | --- | --- |
| Portfolio review | A client/advisor meeting pack: profile, key figures, allocation, performance, risk, income and activity, holdings, and optional reviewed commentary | Implemented end to end (JSON and governed PDF); the primary front-office capability |
| Outcome review | A review-window document for a completed portfolio outcome, composed from the manage-owned bounded report input | Implemented on the shared job pipeline; consumes DPM evidence, never recomputes it |
| Proof pack | A rebalance proof document from the manage- or idea-owned proof-pack report input | Implemented on the shared job pipeline |
| Rebalance wave | A wave execution document from the manage-owned wave report input | Implemented on the shared job pipeline |
| Idea evidence pack | Intake, materialization and exact lost-response recovery for reviewed opportunity evidence | Implemented internal foundation, **not certified**; publication and external support remain blocked |

Implemented is not certified: the
[supported features registry](docs/supported-features.md) is the authoritative,
implementation-backed statement of what is a product capability versus a foundation, and the
[report ordering catalogue](wiki/Report-Ordering.md) governs what a caller may order.

## How a report moves through the platform

```
request (lotus-gateway: entitlement, ordering)
  -> acceptance   lotus-report   durable idempotent job
  -> capture      lotus-report   authoritative reads -> IMMUTABLE SNAPSHOT + source lineage
                                 (lotus-core, lotus-performance, lotus-risk, lotus-ai)
  -> compose      lotus-report   governed semantic report package
  -> render       lotus-render   exact artifact, deterministic PDF production
  -> archive      lotus-archive  durable document custody, retention, access audit
  -> consume      Workbench / gateway consumers
```

Ownership is explicit at every hop: Report owns what the document communicates and the evidence
behind it; Render owns how it looks and is the one archive transmit authority; Archive owns the
stored document and its lifecycle; Gateway owns who may order and retrieve.

Every successful capture also mints a canonical report revision identity that is persisted with
the snapshot and carried through the render package into archive custody. Durable revision
capture does **not** by itself close the full identity chain: the accepted document contract,
trust-state separation, snapshot lifecycle metadata, and the integrated proof remain open under
the governing canonical-identity work
([#283](https://github.com/sgajbi/lotus-report/issues/283), tracked in
[REPOSITORY-ENGINEERING-CONTEXT.md](REPOSITORY-ENGINEERING-CONTEXT.md)).

## Getting started

You need, before anything else:

- **Python 3.12 or newer** — `pyproject.toml` requires `>=3.12` and CI pins 3.12
- **`make`** — every documented command uses it. Not present on Windows by default;
  [Getting Started](wiki/Getting-Started.md) names a verified way to obtain it
- **Docker** — the local run needs a real PostgreSQL, provided by the repository Compose file
- **A virtual environment, activated before `make install`.** `make install` installs into
  whichever interpreter is on `PATH`; it does not create one. On a PEP 668 distribution
  (Debian/Ubuntu, Fedora, Homebrew Python) installing into the system interpreter is refused
  with `externally-managed-environment`. CI does not see this because `actions/setup-python`
  supplies an isolated interpreter.

  Create it with an interpreter you have **verified** is 3.12 or newer, not with whatever
  `python` or `python3` resolves to. Debian 12 ships 3.11 and Ubuntu 22.04 ships 3.10, and
  neither provides a 3.12 package in its default repositories — obtain 3.12+ however suits you
  (pyenv, uv, deadsnakes, python.org, Homebrew, or a newer distribution release), then use that
  interpreter below. Some distributions also package the `venv` module separately, so install the
  one matching your interpreter if `-m venv` reports it missing.

  ```bash
  python3.12 -m venv .venv       # or the full path to your >= 3.12 interpreter
  source .venv/bin/activate
  python -V                      # must report 3.12 or newer before continuing
  ```

  ```powershell
  py -3.12 -m venv .venv         # or the full path to your >= 3.12 interpreter
  .venv\Scripts\Activate.ps1
  python -V                      # must report 3.12 or newer before continuing
  ```

  The `python -V` line is the check that matters: it is the reader's own environment answering,
  rather than this document guessing at it. On a fresh Windows install PowerShell's default
  execution policy blocks `Activate.ps1`; [Getting Started](wiki/Getting-Started.md) carries the
  two ways round it.

Then:

1. Python toolchain and dependencies: `make install`
2. A running PostgreSQL for the report ledgers — the repository Docker Compose provides
   `lotus-report-postgres` on host port `5439`. File databases are not valid runtime evidence.
3. Environment for a local run. Starting through `.venv`'s own interpreter means **no
   activation is required**, so the shell you use only decides how the variables are set:

```powershell
$env:ENTERPRISE_RUNTIME_PROFILE="local"
$env:REPORT_JOB_LEDGER_DATABASE_URL="postgresql://lotus_report:lotus_report@localhost:5439/lotus_report"
$env:PYTHONPATH="src"
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8300
```

```bat
set ENTERPRISE_RUNTIME_PROFILE=local
set REPORT_JOB_LEDGER_DATABASE_URL=postgresql://lotus_report:lotus_report@localhost:5439/lotus_report
set PYTHONPATH=src
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8300
```

```bash
export ENTERPRISE_RUNTIME_PROFILE=local
export REPORT_JOB_LEDGER_DATABASE_URL=postgresql://lotus_report:lotus_report@localhost:5439/lotus_report
export PYTHONPATH=src
.venv/bin/python -m uvicorn app.main:app --reload --port 8300
```

Expected health result:

```bash
curl http://127.0.0.1:8300/health
# {"status":"ok"}
```

Existing supported Report database volumes upgrade in place at startup. If startup fails with a
schema diagnostic, preserve the volume and follow the governed recovery path — see
[Schema Upgrade And Startup Recovery](wiki/Operations-Runbook.md) and
[docs/standards/migration-contract.md](docs/standards/migration-contract.md).

For validation that is safe beside a running local stack, use `make check` (fast local gate) and
`make ci-local` (isolated PR-grade proof against a temporary database) — details and the full
lane model in [Validation and CI](wiki/Validation-and-CI.md). Safe first-response steps for
runtime trouble live in [Troubleshooting](wiki/Troubleshooting.md).

## Where everything else lives

| I want to... | Go to |
| --- | --- |
| See request/response examples for every surface | [wiki/API-Surface.md](wiki/API-Surface.md) |
| Understand the portfolio review contract in depth | [wiki/Portfolio-Review-Report.md](wiki/Portfolio-Review-Report.md) |
| Order reports and integrate as a consumer | [wiki/Report-Ordering.md](wiki/Report-Ordering.md), [wiki/Integrations.md](wiki/Integrations.md) |
| Know what is a certified product capability | [docs/supported-features.md](docs/supported-features.md) |
| Understand the architecture and current priorities | [wiki/Architecture.md](wiki/Architecture.md), [REPOSITORY-ENGINEERING-CONTEXT.md](REPOSITORY-ENGINEERING-CONTEXT.md) |
| Follow a document through its full lifecycle, identities, and failure semantics | [wiki/End-to-End-Report-Lifecycle.md](wiki/End-to-End-Report-Lifecycle.md) |
| Operate, configure, diagnose, and recover the service | [wiki/Operations-Runbook.md](wiki/Operations-Runbook.md) |
| Contribute: workflow, gates, and standards | [wiki/Development-Workflow.md](wiki/Development-Workflow.md), [docs/standards](docs/standards) |
| Security and governance posture | [wiki/Security-and-Governance.md](wiki/Security-and-Governance.md) |

Repository-authored wiki pages under [wiki/](wiki) are the canonical source; the published GitHub
wiki is publication plumbing only.
