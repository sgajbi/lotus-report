# Integrations

## Upstream posture

- `lotus-core`
  portfolio summary, asset allocation, positions, and transaction contracts
- `lotus-performance`
  workspace summary and performance analytics inputs
- `lotus-risk`
  risk analytics from reporting review flows
- `lotus-gateway`
  primary product-facing consumer of reporting payloads
- `lotus-idea`
  planned producer of reviewed opportunity evidence packets for future report evidence-pack intake

## Contract notes

1. integration capability discovery expects snake_case query parameters `consumer_system` and
   `tenant_id`
2. report review risk analytics are derived from daily return streams first produced by
   lotus-performance
3. reporting views must stay faithful to upstream evidence rather than inventing interpretation here
4. the `lotus-idea` evidence-pack intake contract is planned and not certified; it records source
   authority only and must not be treated as live route, render, archive, publication, or supported
   feature proof

## Canonical local identities

- `lotus-report`
  `http://report.dev.lotus`
- `lotus-core query`
  `http://core-query.dev.lotus`
- `lotus-performance`
  `http://performance.dev.lotus`
- `lotus-risk`
  `http://risk.dev.lotus`
