# Troubleshooting

## Common checks

- if reporting payloads look wrong, verify upstream responses before changing local formatting
- if integration capabilities appear incorrect, check `consumer_system` and `tenant_id` query shape
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
