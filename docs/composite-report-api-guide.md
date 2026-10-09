# Composite Review API Guide

The internal calculated-review surface delivers a retained JSON dataset or XLSX
workbook through the existing worker, Render and Archive. Catalogue availability
depends on actual runtime and exact template-format evidence. Qualification is
`EXPLICIT_RETAINED_CALCULATED_REPLAY`, publication state `NOT_ATTESTED` and use
posture `internal_control_only`. This guide does not authorize official
publication, distribution or institutional use. Uncaptured annual, membership,
risk and restatement products stay visible with typed unavailable reasons.

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
| `requested_output_formats` | Select `["json"]` or `["xlsx"]`; retained snapshot is available on either path |
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

An XLSX job becomes `archived` after Render confirms verified Archive custody.
Its existing job response exposes `archive_document_id`, `archive_request_id`
and artifact digest. Read the document through the existing authorized Archive
metadata/download API; Report does not relay document bytes or bypass Archive
access policy. Verify the downloaded SHA-256 against the retained render digest.
A Render or Archive refusal remains a failed/pending job, never a successful
empty workbook. The original request's format remains fixed for that job.

For presentation-only rerender, call `POST /reports/jobs/{job_id}/rerender` with
the same caller context, a new `Idempotency-Key`, and body
`{"reason":"Retained calculated-review presentation rerender"}`. It uses the
original snapshot/template/format and records the Archive old-to-new lifecycle
relationship. Equal retries reuse the attempt and do not fetch Performance again.
This is a technical rerender, not a new financial source version.

For corrected source data, obtain explicit corrected retained pins and submit a
new order/key. The old snapshot is immutable. The generic regenerate/replay
commands currently serve portfolio review only. A corrected composite order
retains its own snapshot/revision/document and cannot rewrite the old dataset,
monthly numbers or old cumulative return. Read both snapshots to compare the
complete accepted source responses; never combine figures across revisions.

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
behavior with controlled Performance. The XLSX package case uses normal shipped
admission and stops at a controlled Render 503 boundary; it tests Report's
package/custody identity and failure behavior, not actual supplier completion.
The owning unit fixture also replays the actual registered Performance response
from merged main with explicitly controlled economic/provider/verifier inputs.
Actual workbook and Archive qualification are separate producer/consumer proofs
recorded in the delivery ledger. These tests do not establish institutional authority.
## Captured calendar and trailing products

The same `POST /reports/composite-reviews` endpoint accepts an optional typed
`source_products` array of 1–8 exact selections. Omit it to preserve the existing
v1 request, identity and retained dataset. An empty array is invalid. Each entry
has a unique `product_key`, an independent `selection` and one of:

- `kind: CALENDAR_RETURN`, with an integer `year` and exactly twelve complete
  January–December monthly pins.
- `kind: TRAILING_RETURN`, with an integer `months` and that exact number of
  complete monthly pins ending on the primary selection's as-of date.

Tenant, composite, currency, fee view, methodology and engine must agree with the
primary selection. Every product pin must deep-match the corresponding primary
window, including materialization, restatement, source cut, hashes, method and
receipt fingerprint. An original calendar response cannot be combined with a
corrected primary. Since-inception and annual dispersion are not selector kinds.
Report calls the existing Performance TWR endpoint for each required selection;
it never links returns locally. Failure, empty HTTP200, changed digest or context
refuses capture and records failed upstream lineage rather than a partial
successful dataset. Transport unavailability retains the existing `unavailable`
call status; semantic refusals retain `error`.

Product orders persist `composite_review.v2` for both input and data contract.
XLSX additionally requires actual readiness for `composite-review/v2`, the v2
data contract and XLSX format before order admission. Missing or incompatible
evidence returns HTTP503 `composite_product_render_unavailable`. JSON orders do
not need a renderer. Accepted contract axes, source calls, snapshot, source-stated
revisions and product custody pins remain immutable. Retained rerender makes no
source request. Existing v1 orders keep their accepted v1 template and identity.

The executable [trailing request](../contracts/examples/composite-review.v2.request.json),
[dataset](../contracts/examples/composite-review.v2.json) and
[schema](../contracts/composite_review.v2.schema.json) use the actual retained R2
two-month controlled synthetic source, with a separately captured trailing product
of the same horizon. Full calendar and trailing regression fixtures use accepted
Performance `75f2f3c585cd7d42075bb8310eced02ecdfad7a9` source evidence.
Run the following from the
`lotus-report` checkout to exercise actual full72/calendar2020/trailing12
original and corrected source captures through the registered Report API/worker:

```powershell
python -m pytest tests/unit/composite_reporting/test_source_products.py -q
```

```bash
python -m pytest tests/unit/composite_reporting/test_source_products.py -q
```

These tests use recorded registered producer responses, SQLite and controlled
Performance transport. They establish test execution, not a live upstream
deployment. All expected values are literal source outputs: original full72
`-0.011980394452`, calendar2020 `-0.002172769524`, trailing12
`-0.008153771882`; corrected full72 `-0.009996419341`, calendar2020
`-0.000169100387`, trailing12 `-0.008153771882`. Source annual member dispersion
returned HTTP422 `ANNUAL_DISPERSION_POLICY_BASIS_MISMATCH`; it supplies no metric
value. Calendar return is not annual member dispersion. All evidence remains
`NOT_ATTESTED`; full #417 and RPT-01–12 acceptance remain open.

The separate R5 integration phase qualifies actual Render/Archive HTTP custody
for the selected v2 calendar/trailing products on Report PR #423, Render PR #345
and Archive PR #183. It uses registered Report ASGI routes with real PostgreSQL
and six recorded accepted Performance responses. Original, financial correction
and retained-original rerender archive successfully; rerender does not refetch.
Independent readers verify all cells, pins and identity before and after Archive
HTTP restart. Fresh-process Report read-only reopening preserves both complete
snapshots and all six upstream call records. See the
[delivery ledger](composite-report-delivery-ledger.md#current-v2-evidence-r5-actual-http-custody)
for exact revisions, manifests, native outcomes and owned backup/cleanup.
This bounded evidence keeps the controlled transport and `NOT_ATTESTED` authority
limits above; it does not establish the remaining source products or full #417.

## Exact linked contribution primary

Use the same `POST /reports/composite-reviews` endpoint with the typed
[`linked_selection` request](../contracts/examples/composite-review.v3.request.json).
Omit TWR `selection` and `source_products`: exactly one primary operation is required.
The [v3 dataset](../contracts/examples/composite-review.v3.json) preserves actual
original supplier output from Performance `6e9bdbb07468d198aaa77a2defd26039f5465fc7`.
The source fixture also retains its explicit financial correction. These are
controlled synthetic, source-owned linked analyses with no institutional attestation.

The request's materialization IDs and complete monthly pins are immutable. Keep
the genuine `restatement_sequence: null`; numeric competing sequences are refused.
Source capture uses `POST /composites/analytics` and the admitted tenant, with no
latest fallback or local linking. Failure or changed source content records failed
lineage and refuses a partial successful dataset. Accepted input/data axes are
`composite_review.v3`. JSON requires no renderer. XLSX requires exact current ready
support for `composite-review/v3`, `composite_review.v3` and XLSX; otherwise admission
returns HTTP503 before source capture. A package or controlled boundary test does
not establish actual Render/Archive completion.

Correction requires a fresh order, corrected complete pins and a new idempotency
key. Retained retrieval uses the captured source and revision identity without
refetching. Existing generic regenerate/replay commands remain portfolio-only.
The same snapshot, source revision and custody digest checks apply to v3.

From the `lotus-report` checkout, in either PowerShell or Bash:

```text
python -m pytest tests/unit/composite_reporting/test_linked_analysis.py -q
```

Tests use registered Report ASGI admission/worker, SQLite and recorded supplier
transport. PostgreSQL integration adds isolated database and fresh-process retention
proof when `REPORT_JOB_LEDGER_DATABASE_URL` is supplied. Neither establishes a live
financial supplier or official publication authority. Full #417 remains open.

## Exact pooled money-weighted primary

Use the same `POST /reports/composite-reviews` with the exclusive typed
[`pooled_selection`](../contracts/examples/composite-review.v5.original.request.json).
The [original](../contracts/examples/composite-review.v5.original.json) and
[corrected](../contracts/examples/composite-review.v5.corrected.json) datasets retain
Performance's complete `composite-pooled-mwr.v1` response. The profile is
`composite_review.v5`; existing v1–v4 selections and captured datasets keep their
original meanings. A monthly eligibility amendment is a separate source product
and is not represented by this calculation-correction profile.

Selection pins the calculation, expected predecessor and its response digest,
tenant/composite/horizon/currency, requested method, engine, manifest and raw bundle
digests, fee view/basis, policy, day basis, fallback election, complete member
population and exact source vector. Capture uses only the retained result GET
`/performance/composites/analytics/results/{calculation_id}`. A correction also
reads its exact selected predecessor and retains both responses. Report submits
no new calculation and has no XIRR, cash-flow, linking or fee calculator.

Configure `LOTUS_PERFORMANCE_READ_BEARER_TOKEN` through the deployment's secret
mechanism. It has no default authority and is not accepted in report request
options. The Performance deployment must verify that credential against its
existing principal trust and grant authority, including the admitted tenant and
every historical member. Report forwards only its configured bearer, admitted
tenant and diagnostic correlation headers; caller Authorization and capability
claims are not forwarded. An unavailable grant authority, missing configuration,
401/403/404/409 response or pending202 refuses successful financial capture. Do not
put credentials in examples, command evidence, snapshots or logs.

JSON orders use the existing durable worker/snapshot lifecycle. XLSX orders require
current ready support for `composite-review/v5`, `composite_review.v5` and XLSX;
until that receiver capability is qualified, admission returns503. A unit-level
package, recorded supplier response or local SQLite reopen is not live Performance
principal, PostgreSQL, Render or Archive acceptance. Full #417 remains open.

From the `lotus-report` checkout, PowerShell or Bash:

```text
python -m pytest tests/unit/composite_reporting/test_pooled_analysis.py tests/unit/composite_reporting/test_pooled_capture.py -q
```
