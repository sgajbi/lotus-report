# Reporting & Aggregation Service

Service scope:
- build aggregated read models for reporting from PAS and PA data contracts
- generate reporting artifacts metadata and download references

## Local Run

```powershell
python -m pip install -e ".[dev]"
$env:PYTHONPATH="src"
uvicorn app.main:app --reload --port 8300
```

API docs:
- http://localhost:8300/docs

## Tests

```powershell
$env:PYTHONPATH="src"
python -m pytest tests -q
```

## Docker

```powershell
docker compose up -d --build
```
