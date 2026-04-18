# API Surface

## Integration

- `GET /integration/capabilities`
  reporting capability publication for downstream consumers

## Aggregations

- `GET /aggregations/portfolios/{portfolio_id}`
  aggregated portfolio rows by as-of date

## Reports

- `POST /reports`
  report metadata generation
- `POST /reports/portfolios/{portfolio_id}/summary`
  lotus-report-owned portfolio summary payload
- `POST /reports/portfolios/{portfolio_id}/review`
  lotus-report-owned portfolio review payload

## Platform surfaces

- `/health`
- `/health/live`
- `/health/ready`
- `/metrics`
- `/docs`

## Current contract notes

- integration capability query parameters are canonical snake_case: `consumer_system`, `tenant_id`
- aggregation query currently uses camelCase alias `asOfDate`
- report summary/review query currently use camelCase alias `sectionLimit`
- summary/review service logic currently accepts both `as_of_date` and `asOfDate` request-body keys
