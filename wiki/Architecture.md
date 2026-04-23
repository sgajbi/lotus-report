# Architecture

## Runtime model

- FastAPI service
- reporting routers under `src/app/routers/`
- orchestration logic under `src/app/services/`
- upstream HTTP clients under `src/app/clients/`

## Code map

- `src/app/routers/integration.py`
  capability publication for downstream consumers
- `src/app/routers/aggregations.py`
  portfolio aggregation read models
- `src/app/routers/reports.py`
  reporting summary/review endpoints
- `src/app/services/reporting_read_service.py`
  summary/review composition from lotus-core, lotus-performance, and lotus-risk
- `src/app/services/aggregation_service.py`
  static and live aggregation logic
- `src/app/models/contracts.py`
  outward contract models and OpenAPI examples

## Boundary notes

1. upstream source truth stays upstream
2. `lotus-report` owns reporting response shape and orchestration behavior
3. capability publication uses snake_case query parameters today
4. public API surfaces publish canonical snake_case request, query, and response fields only
5. stale placeholder routes should be removed rather than documented as product capabilities
