# Archive lineage acknowledgement and recovery

Report owns correction and replacement intent. Archive owns retained documents and lifecycle
relationships. A verified new document remains archived while linkage is uncertain; linkage
recovery does not render, recollect or transmit another artifact.

## Confirmation boundary

Report confirms `POST /documents/{source}/correct` or `/supersede` only when HTTP 200/201
contains a string `lifecycle_relationship_id` with nonblank content and at most 256 characters,
plus the exact requested source, target and transition. The identifier limit is a Report consumer
budget, not an Archive identifier format or prefix rule. Confirmed events retain the validated
relationship ID and requested pair; arbitrary response fields are not copied.

Archive replays known pairs with their original identity, reason, actor and timestamp. These need
not equal a retry caller's values. `current_document_id` may advance beyond the historical target;
it does not rebind the pair. Custody and tenant ownership remain separately admitted facts.

## Outcomes and settlement

| Outcome | Durable event | Recovery |
| --- | --- | --- |
| Matching 200/201 acknowledgement | `job_archive_lineage_recorded`, validated relationship ID and pair | Pair leaves reconciliation |
| Missing/malformed/foreign 200/201 acknowledgement | `job_archive_lineage_pending`, `archive_lineage_acknowledgement_invalid` and HTTP status | Retry the same pair after recovery |
| Communication failure, 503 or other nonterminal status | `job_archive_lineage_pending`, `archive_lineage_call_unconfirmed` and status (0 for exception) | Retry the same pair |
| 4xx contract/policy refusal | `job_archive_lineage_refused` and status | Operator investigation; excluded from automatic retry |

Existing pair keys remain `{transition}:{source}->{target}`. Pending and confirmed appends
deduplicate under existing keys. Later invalid attempts leave the first pending event in place;
its status/reason describes that admission, not a latest-attempt log. The bounded worker pass
reuses persisted job context and the same pair. Rerender entry also settles pending pairs;
same-key regenerate retries converge through Archive replay. Settlement confirms one pair and
stops reconciliation without another correction intent or changes to snapshot bytes/hash.

## Previously recorded but unverified relationships

Earlier recorded events may lack `lifecycle_relationship_id`. The new guard cannot establish
which historical calls returned valid replies. Those events still suppress automatic
reconciliation under the append-only contract. This slice does not rewrite history, bulk replay
transitions or certify historical pairs.

Operators should:

1. Read authorized `GET /reports/jobs/{job_id}/events` and diagnostics. Select recorded lineage
   events lacking relationship IDs and retain the exact job/source/target/transition. Absence is
   a review candidate, not proof that Archive lacks the relationship.
2. Inspect Archive's authorized `GET /documents/{source}/source-events` and historical metadata
   for the exact relationship. Resolve the current chain separately; an advanced current document
   does not disprove a valid historical pair. Retain source-owned evidence.
3. For a demonstrated missing/uncertain pair, obtain normal Archive lifecycle authority and
   coordinate a scoped idempotent request for the exact pair with Archive ownership, especially
   when later transitions exist. Verify the acknowledgement and retain the repair record in the
   operator case associated with the Report job.
4. Keep original events and artifacts intact. This slice exposes no historical reclassification
   or administrative repair command. Escalate ledger correction as a separate governed change
   rather than editing database rows.

## Verification boundary

Native tests exercise the production client/parser under malformed success, valid creation/replay,
terminal refusal and transient failure. Registered Report and controlled Archive socket tests use
real SQLite job/snapshot adapters, recreate the ledger between stages, verify 503 -> empty success
-> valid recovery, and retain snapshot bytes/hash and archived job metadata. Capture and Render
remain controlled fixtures. This proves Report admission/recovery, not live Archive storage, PDF
bytes, upstream arithmetic, PostgreSQL process recovery, bank IAM or completed historical repairs.
