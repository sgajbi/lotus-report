# Getting Started

## Install

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
