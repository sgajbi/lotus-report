# Monthly source-amendment JSON capture

Report consumes Manage's ordinary-month `SOURCE_CORRECTION` through the existing
`POST /reports/composite-reviews`, durable worker and immutable snapshot. This
increment is JSON-only and `NOT_ATTESTED`; full #417 remains open. No financial
supplier qualification, bank IAM, publication authority or new runtime is implied.
V6 contains source-correction eligibility evidence and supplies no TWR, MWR,
dispersion, contribution or model-fee calculation. `report_facts.unavailable`
describes eligibility availability; it conveys no financial authority.

## Independent versions and migration

| Axis | Meaning |
| --- | --- |
| `composite_review.v6` | Additive dataset, reserved after checking v1–v5 registry and open PRs. |
| `eligibility_selection.selection_version: "v2"` | Explicit typed variant under the existing selector. |
| Monthly proposal/approval/receipt `product_version: "v2"` | Manage source-correction contracts. |
| Embedded definition `product_version: "v1"` or `"v2"` | Independent definition axis, retaining its own hash convention. |

Historical requests omit `selection_version` and still select v4. Their serialized
request, fingerprint, schema/examples, retrieval and technical rerender remain
unchanged. A v2 request cannot omit its tag and silently become historical. No
endpoint, parallel primary selector, database migration or template fallback is added.

## Executable examples

`contracts/examples/composite-review.v6.definition-v1.published.request.json` and
`composite-review.v6.definition-v2.published.request.json` have matching
`.expected.json` datasets. The `.evaluated.request.json` variants retain unapproved
proposals, with approval/publication unavailable. `.source.json` bundles are
controlled in-memory domain examples from qualified Manage main
`545269b3d356630680da7c692d90613a5c28f99c`, not native HTTP source proof.

From the Report checkout, PowerShell:

```powershell
$request = Get-Content -Raw contracts/examples/composite-review.v6.definition-v1.published.request.json
$headers = @{'X-Actor-Id'='internal-reviewer';'X-Caller-Application'='lotus-gateway';'X-Tenant-Id'='synthetic-tenant';'X-Region'='APAC';'Idempotency-Key'='monthly-amendment-example'}
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8106/reports/composite-reviews -ContentType application/json -Body $request -Headers $headers
```

From the same checkout, Bash:

```bash
curl -X POST http://127.0.0.1:8106/reports/composite-reviews \
  -H 'Content-Type: application/json' -H 'X-Actor-Id: internal-reviewer' \
  -H 'X-Caller-Application: lotus-gateway' -H 'X-Tenant-Id: synthetic-tenant' \
  -H 'X-Region: APAC' -H 'Idempotency-Key: monthly-amendment-example' \
  --data-binary @contracts/examples/composite-review.v6.definition-v1.published.request.json
```

These requests require an independently configured Report service and exact retained
source evidence; they do not seed a producer. Explicit Manage reader enrollment is
required. Order returns `202`; the ordinary worker captures the selected source.
Retrieve `GET /reports/jobs/{report_job_id}/snapshot`. Identical retries reuse the
job without recollection; changed content conflicts, foreign retrieval returns `404`.

## Lineage dictionary

The selector retains tenant/composite/definition/currency/horizon and month pins.
Published months additionally pin approval/receipt, canonical membership/universe/
projection parent/publication and complete response digests.

| Field | Required meaning |
| --- | --- |
| `lineage_receipts` | Nearest predecessor first, ending at an ordinary v1 monthly receipt; 1–31 exact receipts per selected month. |
| Each receipt pin | `product_version`, `evaluation_revision`, `approval_content_hash`, `receipt_content_hash`, `receipt_response_digest`. |
| `parent_publication_response_digest` | Published only; protects the canonical projection-parent publication. |
| `correction_kind` | Exactly `SOURCE_CORRECTION`. |
| Predecessor approval/receipt bindings | Exact product/version/revision/digest of the immediately replaced approved month. |
| `original_approval_binding` | Exact ordinary monthly v1 root reached through the complete chain. |
| `expected_authority_binding` | Equals predecessor approval; no latest/timestamp inference. |
| `projection_parent_membership_binding` | Equals proposal parent revision/hash and predecessor receipt membership binding. |
| `expected_current_publication_sequence` | Equals predecessor and canonical projection-parent sequence; intervening dependent-month projection is unsupported. |
| `affected_from`, `affected_to` | Inclusive first/last dates of the same calendar month. |
| `reason_code`, `reason`, `evidence_bindings` | Nonempty source explanation and unambiguous product/version/revision/digest evidence, retained completely. |

Capture uses exact approved binding POSTs to
`/api/v1/rebalance/composites/{composite_id}/definitions/{definition_version}/eligibility-evidence/resolve`
and existing canonical GETs. Evaluated-only also reads the exact proposal. Full raw
wires and source content/response digests survive; revision identity includes the
selected and predecessor receipts. Existing 8 MiB whole-capture/dataset bounds apply.
Report's smaller lineage bound refuses excess history instead of truncating it.
Amendment rows use the declared claim/binding field order, preserving the published
wire when JSON transports or PostgreSQL JSONB reorder object keys. Source arrays
retain their original order; object ordering has no authority or hash meaning.

## Projection and availability

Eight eligibility tables retain counts, members, all three assessments, every
reason occurrence and canonical membership history. Ninth table `Amendments`
projects complete selected/predecessor amendment claims and receipt identities
through source-pointer cells. Full predecessor source graphs remain in
`source_months[].lineage_receipts`. Source-stated status/reason, discretionary fact,
approval reference and evaluation identity must match projected decisions. Report
does not evaluate rules or join eligibility to old Performance aggregates.

| Availability | Meaning |
| --- | --- |
| `AVAILABLE` | Captured scalar, including zero and false. |
| `NOT_APPLICABLE` | Known empty reason set or unused rule field; explicit reason retained. |
| `UNAVAILABLE` | Source null or evaluated-only approval/history absent; never replaced by zero. |
| `NOT_ATTESTED` | Synthetic unsigned approvals, unverified population/completeness and unavailable official activation remain explicit. |

Staged roots, cascades, changed policy/population, absent/cyclic/oversized lineage,
unchanged source and conflicting identity/hash/scope/date/sequence/authority refuse.
Worker failure retains evidence and does not create a valid data-ready dataset.

## Operation and proof boundaries

Malformed selectors fail before durable intake. V6 XLSX orders return `503` with
`composite_amendment_render_unavailable`; retained package construction also refuses.
Render #352 and Archive #188 require explicit v6 contracts, identity and custody
before XLSX support can be promoted. JSON does not prove downstream delivery.
Any future v6 XLSX disclosure must retain this eligibility-only scope. Financial
metrics and their availability belong to the separately governed financial family.

From the Report checkout, in PowerShell or Bash:

```text
python -m pytest tests/unit/composite_reporting/test_monthly_amendment.py -q
python -m pytest tests/integration/test_composite_amendment_postgres_retention.py -q
```

Integration uses the repository's caller-owned/session-isolated PostgreSQL flow
when `REPORT_JOB_LEDGER_DATABASE_URL` is provided. Intake is registered in-process
ASGI; Manage transport is controlled fixture data. Actual PostgreSQL worker and
separate-process reopening qualify Report persistence only. Existing immutable
JSON snapshots carry v6 without a database migration.
