# Composite Review API Guide

The current internal surface delivers a retained JSON dataset. Excel remains
unavailable in the public catalogue. This guide does not authorize official
publication, distribution or institutional use.

## Source selection and order

Obtain a tenant-owned, explicit retained calculation from Performance and retain
its complete response and selection manifest. Build `selection` with the exact
calculation/composite, horizon, currency, fee view, methodology, engine version,
calculation fingerprint and ordered window pins. `response_digest` is Report's
SHA-256 of canonical JSON (`sort_keys=True`, compact separators, exact source
financial text); it is transport evidence, not approval authority.

Prepare `request.json` with these fields for `POST /reports/composite-reviews`:

| Field | Content |
| --- | --- |
| `selection` | Complete typed `CompositeReportSelection` object built from the actual retained source |
| `requested_output_formats` | `["json"]` |
| `options` | Optional governed retention fields; `{}` when none are requested |

The shared
[synthetic example](../contracts/examples/composite-review.v1.json) contains a
complete `selection` object for the executable controlled-source tests. Never
send it as real or official source evidence.

Required headers are `X-Tenant-Id`, `X-Actor-Id`, `X-Caller-Application`, `X-Region`
and `Idempotency-Key`. Optional `X-Correlation-Id` and `X-Trace-Id` propagate
through capture; middleware generates durable identities when omitted.
The body tenant must match admitted caller context. Equal retries reuse the same
job; changed content under the same key returns 409. Missing key or conflicting
tenant returns 400, malformed selection or unsupported format returns 422.

From the directory holding `request.json`, set `LOTUS_REPORT_BASE_URL`,
`LOTUS_TENANT_ID`, `LOTUS_ACTOR_ID`, `LOTUS_CALLER_APPLICATION`, `LOTUS_REGION`
and `LOTUS_RETRY_KEY` to your admitted environment and submit:

```powershell
curl.exe --fail-with-body --silent --show-error `
  -X POST "$env:LOTUS_REPORT_BASE_URL/reports/composite-reviews" `
  -H 'Content-Type: application/json' `
  -H "X-Tenant-Id: $env:LOTUS_TENANT_ID" `
  -H "X-Actor-Id: $env:LOTUS_ACTOR_ID" `
  -H "X-Caller-Application: $env:LOTUS_CALLER_APPLICATION" `
  -H "X-Region: $env:LOTUS_REGION" `
  -H "Idempotency-Key: $env:LOTUS_RETRY_KEY" --data-binary '@request.json'
```

```bash
curl --fail-with-body --silent --show-error \
  -X POST "$LOTUS_REPORT_BASE_URL/reports/composite-reviews" \
  -H 'Content-Type: application/json' \
  -H "X-Tenant-Id: $LOTUS_TENANT_ID" \
  -H "X-Actor-Id: $LOTUS_ACTOR_ID" \
  -H "X-Caller-Application: $LOTUS_CALLER_APPLICATION" \
  -H "X-Region: $LOTUS_REGION" \
  -H "Idempotency-Key: $LOTUS_RETRY_KEY" --data-binary '@request.json'
```

The response is 202 with `report_job_id` and links to the existing job surfaces.
It acknowledges durable admission, not completed financial capture. The worker
calls Performance with exact ordered `materialization_ids`; it never substitutes
latest facts or calculates investment returns.

## Inspect retained results

Read `GET /reports/jobs/{job_id}` with the same caller context for status.
Read `GET /reports/jobs/{job_id}/snapshot` for the retained dataset, hash, revision
and source vector. A successful JSON job becomes `data_ready`. Refused source
evidence produces a failed capture with bounded failure information and upstream
response hash, with no financial response or revision. A foreign tenant receives
404 on retained reads.

Canonical financial text and each cell's exact source pointer remain authoritative.
Source +1% is stored as `0.010000000000`; the first two monthly source facts +1%
and +2% retain the Performance-owned cumulative `0.030200000000`. Report echoes
these values. Null risk/dispersion or uncaptured annual/member/restatement products
carry availability and reasons, never zero or fabricated populated sheets.

For corrected source data, obtain explicit corrected retained pins and submit a
new order/key. The old snapshot is immutable. The generic regenerate/replay
commands currently serve portfolio review only. Real composite workbook and
Archive/rerender acceptance remains required before support is exposed.

## Executable controlled-source proof

From the `lotus-report` checkout, with its documented development environment:

```powershell
python -m pytest tests/unit/composite_reporting -q
```

```bash
python -m pytest tests/unit/composite_reporting -q
```

For actual PostgreSQL Report custody, set `REPORT_JOB_LEDGER_DATABASE_URL` to a
caller-owned test PostgreSQL server, then run from the same checkout:

```powershell
python -m pytest tests/integration/test_composite_postgres_retention.py -q
```

```bash
python -m pytest tests/integration/test_composite_postgres_retention.py -q
```

The integration fixture provisions and drops an isolated session database. Its
registered route/worker and separate-process read are real Report/PostgreSQL
behavior with controlled Performance. The candidate package case supplies a
test-only XLSX family definition to normal validation and stops at a controlled
Render boundary. It does not certify actual Performance, workbook or Archive.
