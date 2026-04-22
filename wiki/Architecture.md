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
  report generation plus reporting summary/review endpoints
- `src/app/services/reporting_read_service.py`
  summary/review composition from lotus-core, lotus-performance, and lotus-risk
- `src/app/services/aggregation_service.py`
  static and live aggregation logic
- `src/app/models/contracts.py`
  outward contract models and alias behavior

## Boundary notes

1. upstream source truth stays upstream
2. `lotus-report` owns reporting response shape and orchestration behavior
3. capability publication uses snake_case query parameters today
4. the first-class portfolio review endpoint publishes snake_case request, query, and response
   fields only; remaining legacy aliases on other surfaces should be handled as cleanup debt
