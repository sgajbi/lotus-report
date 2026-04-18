# Getting Started

## Install

```bash
make install
```

## Run locally

```powershell
$env:PYTHONPATH="src"
uvicorn app.main:app --reload --port 8300
```

Canonical identities:

- cross-app validation: `http://report.dev.lotus`
- direct process debugging: `http://127.0.0.1:8300`

## First checks

```powershell
curl http://127.0.0.1:8300/health
curl "http://127.0.0.1:8300/integration/capabilities?consumer_system=lotus-gateway&tenant_id=default"
```

If the process is up but reporting calls still fail, check upstream base URLs in `src/app/config.py`
before debugging payload formatting.

## First docs to read

- [README.md](../README.md)
- [REPOSITORY-ENGINEERING-CONTEXT.md](../REPOSITORY-ENGINEERING-CONTEXT.md)
- [docs/standards/data-model-ownership.md](../docs/standards/data-model-ownership.md)
- [docs/operations/development-workflow-and-ci-strategy.md](../docs/operations/development-workflow-and-ci-strategy.md)
