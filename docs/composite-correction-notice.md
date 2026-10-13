# Retained same-scope composite correction notice

`composite_review.v9` is a local producer candidate for an internal XLSX notice
comparing two exact retained `composite_review.v8` generations. It uses the existing
composite order, durable worker, immutable snapshot, Render package and replay paths.
It remains `PARTIAL` and `NOT_ATTESTED`. Its relationship wire contract is frozen
against Archive197 commit `036c14127369c99f28448d253d4495a1ae2b58c3`, including the
exact native eight-field schema, operation and 423-byte KAT accepted in
[the bounded reconciliation grant](https://github.com/sgajbi/lotus-archive/issues/197#issuecomment-6116899475).
This offline contract acceptance does not establish live cross-service delivery.

## Order and source authority

`POST /reports/composite-reviews` accepts an exclusive `notice_selection`; the
request cannot also select another composite primary or supply raw notice evidence
through options. Only `requested_output_formats: ["xlsx"]` is supported. Admission
requires current exact `composite-review/v9` Render support, otherwise returns 503.
The normal caller context and idempotency rules apply: the same key and content
reuse the job; changed content under the same key returns 409.

The closed selector contains:

| Field | Required evidence |
| --- | --- |
| `comparison_profile` | `RETAINED_SAME_SCOPE_CORRECTION_NOTICE_V1` |
| `tenant_id`, `composite_id` | Exact source scope |
| `period_start`, `period_end`, `reporting_currency` | Exact shared horizon and currency |
| `before`, `after` | Each has `report_job_id`, `snapshot_id`, `report_revision_id`, `snapshot_hash` |
| `source_document_ids` | Four distinct IDs ordered O, O_rr, C, C_rr |
| `relationship_ids` | Three distinct IDs ordered O→O_rr, O_rr→C, C→C_rr |

Capture authorizes both persisted source jobs against tenant, region and booking
centre before reading their snapshots or Archive evidence. Each source must retain
`snapshot_recomposition` availability. Native v8 revision, source vector, factual
digest, capture identity and full source custody are verified independently.
Both generations must also share return view, methodology and engine version.
No latest lookup or financial service call substitutes for either retained capture.

Archive reads are the four exact document GETs plus the reviewed privileged
`GET /documents/{document_id}/lifecycle-relationships/{lifecycle_relationship_id}`.
The latter requires Archive's existing Report-only lifecycle permission and source
and target segregation. Report's fixed service context does not grant that permission.
Its candidate acknowledgement kind is `ARCHIVE_RELATIONSHIP_RECORD` with exactly
eight fields: relationship ID, source and target document IDs, transition type,
reason, reason code, requested actor and aware requested timestamp. Dynamic
`current_document_id` and caller-supplied receipts are rejected. Technical edges
require unique acknowledged Report lineage events with matching generation and
relationship identity; they cannot substitute for the economic correction edge.

Current snapshot response bytes use the native snapshot API representation after
authorization. This does not recover historical HTTP bytes. Archive response bytes
are measured from the actual bounded HTTP stream. The aggregate capture budget
includes both snapshots, both Report event responses, four document responses and
the relationship response. Failures are terminal, bounded validation reasons;
supplier text and foreign identifiers are not returned in support messages.

## Dataset, hashes and workbook projection

The dataset retains both complete native v8 payloads, their original four-field
selection tuples and capture/source identities. The 14 tables from each side are
copied completely: only table IDs and source pointers gain a side prefix. Original
financial strings, null availability, reason codes, order and source policies remain
unchanged. Four additional tables are `NoticeScope`, `PairIndex`, `ArchivePath` and
`Obligations`, for 32 semantic tables. `PairIndex` is the complete coordinate union,
including one-sided cells and actual cell references. Report derives no change,
materiality or interaction arithmetic.

The versioned pair and manifest recipes use sorted compact ASCII JSON, preserve
array order and reject non-finite values. Separate domain hashes bind documents and
acknowledgements. Admission reconstructs source facts, the coordinate union and all
tables independently of submitted hashes, so resealing altered evidence does not
make it admissible. Recipes live in `correction_notice/hashing.py`; the unchanged
projection recipe ID is `report446-v9-32-table-projection.r4`.

The eight independent obligations cover before/after results, affected periods,
reason, materiality, report approval, report replacements, explanation/delta/
interaction and recipient distribution. Retained results, horizon and acknowledged
path are internal captured evidence. The other obligations remain explicitly
unavailable. Archive correction reason text supplies no report-publication reason,
materiality decision, approval or recipient register.

The existing literal workbook planner gains six source-coordinate fields on
`CellEvidence` and six notice identity fields on `ArtifactIdentity`. Source JSON
chunking, row partitioning, UTF-16 cell limits and all capacity limits are unchanged.
The immutable test pair produces 47 sheets, 150,148 cells and 12,777 rows; native
producer preflight measures a 4,824,578-byte request. These measurements establish
producer fit only. They do not establish actual XLSX output size or receiver behavior.

The notice mints its own job, snapshot and revision identity. Its Archive custody
contains both six-field source anchors: the original four-field tuple plus source
document ID and concrete native v8 source identity. O and C anchor these source
identities. Report's snapshot hash is verified from Report; Archive does not store it.

## Retained replay and validation

Accepted v9 retained replay uses the stored notice payload and its exact template,
revision, source vector and custody. It does not invoke Performance or require a
new TWR invocation binding. Existing v8 replay still requires its binding. Controlled
registered API tests cover idempotency, capture, native snapshot-read bytes, tenant
refusal, SQLite reopen, complete package construction and source-off replay with
controlled Archive and renderer ports. A SQLite reopen is not a process-restart or
PostgreSQL qualification claim.

Working directory: the `lotus-report` checkout, with constrained development
dependencies installed. The following commands work in PowerShell and Bash:

```text
python -m pytest tests/unit/test_composite_correction_notice.py tests/unit/test_composite_notice_capture.py tests/unit/test_composite_notice_archive_transport.py tests/unit/test_composite_notice_registered_capture.py -q
python -m mypy --config-file mypy.ini
python scripts/openapi_quality_gate.py
```

The [fixture guide](../tests/fixtures/composite-correction-notice/README.md) documents
the write-once local evidence generator and provenance. Its r6 relationship record
is explicitly controlled test evidence; the original historical POST bytes remain
separate. The generator emits schemas, complete package, canonical preimages,
known answers, budget proof and 15 individually resealed refusal cases. An optional
`--r5-baseline` compares protected generations, all source-side tables, coordinate
union, obligations, document nodes, technical acknowledgements and source custody.

Actual privileged history GET, Archive security/audit/PostgreSQL qualification,
joined runtime delivery and hard-crash recovery remain qualification gates.
Local installed receiver and writer fit evidence is recorded separately in the
qualified handoff; it does not establish these runtime claims.
This producer candidate does not close Report446 or confer official
publication, replacement authority, distribution or full lineage-product support.
