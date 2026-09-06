# Getting Started

## Install

Prerequisites, before anything else:

- **Python 3.12 or newer** — `pyproject.toml` requires `>=3.12` and CI pins 3.12
- **`make`** — every documented command uses it, and Windows does not ship it. On the
  workstation this was verified on, it comes from WinGet:

  ```powershell
  winget install ezwinports.make
  make --version                 # GNU Make 4.4.1 here
  ```

  That is the package providing the working binary on this machine, confirmed by resolving
  `make` to `…\WinGet\Links\make.exe` and querying `winget list --id ezwinports.make`. It is
  **not** the only route — Chocolatey, Scoop and MSYS2 also supply GNU Make and are untested
  here — and a clean install on a machine without `make` has not been performed, so treat this
  as the verified-working option rather than a guaranteed one.

- **A POSIX shell, which `make` alone does not provide.** Recipes here use POSIX syntax —
  `Makefile:62` runs `COVERAGE_FILE=… python …`, a shell assignment `cmd.exe` would treat as
  a command name. GNU Make runs recipes through `SHELL`, and with only the WinGet package on a
  host that has no POSIX shell that resolves to the Windows interpreter and the recipe fails.

  On this workstation it resolves to Git Bash, which is why the targets work here:

  ```shell
  make -f probe.mk shellcheck      # SHELL=/usr/bin/sh ; POSIX assignment worked
  ```

  GNU Make uses `sh.exe` **if it can find one on `PATH`**, and falls back to the Windows
  interpreter otherwise. Installing Git for Windows is therefore necessary but not sufficient:
  its `bin` directory must be on `PATH`, which the installer's "Git from the command line and
  also from 3rd-party software" option does. Running `make` from Git Bash guarantees it.

  Both halves are measured here, with the same probe recipe:

  ```
  PATH including Git bin   ->  SHELL_IS=/usr/bin/sh          (also from PowerShell)
  PATH excluding Git bin   ->  SHELL_IS=$0   (unexpanded - cmd.exe ran the recipe)
  ```

  Confirm your own setup before running the gates:

  ```shell
  printf 'probe:
	@echo SHELL_IS=$$0
' > probe.mk && make -f probe.mk probe
  # must print SHELL_IS=/usr/bin/sh or similar; SHELL_IS=$0 means cmd.exe and the gates will fail
  ```
- **Docker** — the local run needs a real PostgreSQL ledger, not a file database. The
  repository Compose file provides `lotus-report-postgres` on host port `5439`; bring it up
  with `docker compose up -d lotus-report-postgres` before running the service
- **An activated virtual environment.** `make install` installs into whichever interpreter is
  on `PATH`; it does not create one. On a PEP 668 distribution (Debian/Ubuntu, Fedora,
  Homebrew Python) installing into the system interpreter is refused with
  `externally-managed-environment`. CI does not see this because `actions/setup-python`
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
  rather than this document guessing at it.

  **Windows: PowerShell's default execution policy blocks `Activate.ps1`.** A fresh install is
  `Restricted`, so activation fails before `make install` runs. Either allow signed and local
  scripts for your own account only — the scope Python's venv documentation recommends —

  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```

  or skip PowerShell scripting entirely and activate from `cmd.exe`, which needs no policy
  change:

  ```bat
  .venv\Scripts\activate.bat
  ```

  Prefer the second if you would rather not change a security setting to install a project. Both
  end in the same activated environment, and `python -V` confirms it either way.

```bash
make install
```

## Run locally

```powershell
$env:ENTERPRISE_RUNTIME_PROFILE="local"
$env:PYTHONPATH="src"
uvicorn app.main:app --reload --port 8300
```

Canonical identities:

- cross-app validation: `http://report.dev.lotus`
- direct process debugging: `http://127.0.0.1:8300` with `ENTERPRISE_RUNTIME_PROFILE=local`

## First checks

```powershell
curl http://127.0.0.1:8300/health
curl "http://127.0.0.1:8300/integration/capabilities?consumer_system=lotus-gateway&tenant_id=default"
```

If the process is up but reporting calls still fail, check upstream base URLs in `src/app/config.py`
before debugging payload formatting.

## First portfolio review probe

Use the governed front-office portfolio when validating the portfolio review report:

```powershell
curl -X POST "http://127.0.0.1:8300/reports/portfolios/PB_SG_GLOBAL_BAL_001/review?section_limit=20" `
  -H "Content-Type: application/json" `
  -H "X-Correlation-ID: portfolio-review-local-proof" `
  -d "{\"as_of_date\":\"2026-04-22\",\"reporting_currency\":\"USD\",\"benchmark_code\":\"BMK_PB_GLOBAL_BALANCED_60_40\",\"sections\":[\"CLIENT_PROFILE\",\"OVERVIEW\",\"ALLOCATION\",\"PERFORMANCE\",\"RISK_ANALYTICS\",\"INCOME_AND_ACTIVITY\",\"HOLDINGS\",\"TRANSACTIONS\"]}"
```

Read [Portfolio Review Report](Portfolio-Review-Report) before changing this endpoint. The endpoint
is a governed meeting-pack contract, so response shape, sourced figures, missing-data behavior,
advisor-only separation, and AI guardrails all matter.

## First docs to read

- [README.md](https://github.com/sgajbi/lotus-report/blob/main/README.md)
- [REPOSITORY-ENGINEERING-CONTEXT.md](https://github.com/sgajbi/lotus-report/blob/main/REPOSITORY-ENGINEERING-CONTEXT.md)
- [docs/standards/data-model-ownership.md](https://github.com/sgajbi/lotus-report/blob/main/docs/standards/data-model-ownership.md)
- [docs/operations/development-workflow-and-ci-strategy.md](https://github.com/sgajbi/lotus-report/blob/main/docs/operations/development-workflow-and-ci-strategy.md)
- [Portfolio Review Report](Portfolio-Review-Report)
