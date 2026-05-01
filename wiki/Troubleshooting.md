# Troubleshooting

## Common checks

- if reporting payloads look wrong, verify upstream responses before changing local formatting
- if integration capabilities appear incorrect, check `consumer_system` and `tenant_id` query shape
- if evidence-surface posture looks degraded, check the `supportability` object from
  `GET /integration/capabilities` before investigating individual report jobs
- `/metrics` exposes `lotus_report_evidence_surface_supportability_total` with only bounded
  `state`, `reason`, and `freshness_bucket` labels; it must not include report, portfolio, client,
  tenant, trace, or correlation identifiers
- unexpected evidence-surface posture values are intentionally folded to `state="unsupported"`,
  `reason="supportability_unsupported"`, and `freshness_bucket="unknown"` so operators can see the
  degraded posture without leaking client, portfolio, report, tenant, trace, or raw upstream values
- if aggregation requests fail, confirm `as_of_date` query handling and live/static mode intent
- if summary/review requests fail validation, confirm request body date field shape and section limit

## Useful commands

```bash
make check
make ci
```

## References

- [docs/standards/data-model-ownership.md](../docs/standards/data-model-ownership.md)
- [docs/operations/development-workflow-and-ci-strategy.md](../docs/operations/development-workflow-and-ci-strategy.md)
