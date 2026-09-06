# Getting Started

## Install

Prerequisites, before anything else:

- **Python 3.12 or newer** — `pyproject.toml` requires `>=3.12` and CI pins 3.12
- **`make`** — every documented command uses it; not a default on Windows
- **Docker** — the local run needs a real PostgreSQL ledger, not a file database. The
  repository Compose file provides `lotus-report-postgres` on host port `5439`; bring it up
  with `docker compose up -d lotus-report-postgres` before running the service
- **An activated virtual environment.** `make install` installs into whichever interpreter is
  on `PATH`; it does not create one. On a PEP 668 distribution (Debian/Ubuntu, Fedora,
  Homebrew Python) installing into the system interpreter is refused with
  `externally-managed-environment`. CI does not see this because `actions/setup-python`
  supplies an isolated interpreter.

  Use a **versioned** interpreter: on Debian 12 and Ubuntu 22.04 `python3` is 3.11 or 3.10 even
  when 3.12 is installed alongside, and a venv built from it fails the editable install.

  ```bash
  # Debian/Ubuntu: sudo apt install python3.12-venv
  python3.12 -m venv .venv
  source .venv/bin/activate
  python -V                      # must report 3.12 or newer before continuing
  ```

  ```powershell
  py -3.12 -m venv .venv
  .venv\Scripts\Activate.ps1
  python -V                      # must report 3.12 or newer before continuing
  ```

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
