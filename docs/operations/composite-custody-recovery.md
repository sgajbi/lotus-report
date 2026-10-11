# Initial composite custody recovery

For an accepted `composite_review.v8` XLSX job whose initial Archive handoff failed,
use the existing `POST /reports/jobs/{job_id}/replay` operator command with trusted caller
context, a bounded reason and a stable `Idempotency-Key`. This is failed-work recovery;
presentation changes to already archived jobs continue through the rerender command.

| Source outcome | Recovery behavior |
| --- | --- |
| Pending deadline or ambiguous failed handoff, recorded Archive request | Resolve that exact request before replacement work |
| Matching committed document | Adopt the original document; source becomes archived; replay returns 409 because replacement is unnecessary |
| HTTP 404 with Archive's `error.code=document_not_found` | Clone retained snapshot facts and render one replacement through Render's existing handoff |
| Unavailable lookup, malformed/mismatched identity, missing retained evidence | Refuse with 409; no replacement render or source acquisition |
| Definitive Archive 4xx refusal | Source remains non-retryable pending operator action |
| Missing, malformed or unrecognized Render custody state | Preserve any returned request ID and resolve it first; this does not prove that delivery was never attempted |
| Unresolved custody without an original request ID | Non-retryable; operator reconciliation required, with no identity inferred from unknown-state artifact bytes |

Adoption binds tenant, region, composite scope, original job/request/snapshot/render identifiers,
template, output/data contract, reporting dates, Report revision and the complete composite custody
identity, classification, frequency and every supplied retention field. Adoption also requires
the retained original Render artifact digest and matching Archive checksum/declaration; a missing
digest refuses rather than weakening custody verification. Report never uploads bytes to Archive
or creates a new Archive request identity.
The retained digest must contain exactly 64 hexadecimal characters, optionally preceded by one
`sha256:` prefix. Missing, malformed or conflicting inherited source-authority bindings refuse
with 409 before adoption or derived-job creation; recovery never obtains a replacement grant.

The historical `archive_handoff_not_configured` category includes invalid or missing handoff
state. It is subject to the same resolution-first and replacement-lineage fences as other
ambiguous custody. No distinct no-attempt recovery is authorized by a null state. Legacy identity
derivation applies only to recognized custody states from responses predating the request-ID field;
an explicitly missing/malformed ID or unknown state never enables that fallback.

The absence predicate uses Archive's existing by-request-id error contract at accepted producer
pin `ec85f69ca8708f1d81b97b255538ac24b8683b3a`. HTML, empty/non-object bodies, wrong error codes,
binary-missing responses and malformed error objects cannot release a replacement. Resolver
timeouts and exceptions also refuse before source reads, job creation, rendering or upload.

Recovery preserves accepted contract, selection, optional return products, supplied source context,
snapshot bytes/hash, series/revision/factual digests and source revision vector. It inherits the
original accepted source-authority admission and binds that provenance to the derived job; it does
not acquire a new credential or call Performance/Manage. Snapshot lineage counters describe zero
new source calls and point back to retained source evidence.

Repeated keys converge on the same derived job. PostgreSQL serializes creation on the existing
source/lineage locks; another key cannot create a replacement while one is active, archived or has
an unresolved custody outcome. A crash after snapshot cloning resumes from that clone with the
same key. A committed adoption remains terminal even if recording its audit event is interrupted.
Retained source evidence is required for composite replay, including repeated commands. Source
retention loss requires operator investigation rather than reconstruction from current sources.

This scope covers v8 XLSX initial custody only. Controlled transport tests and owned PostgreSQL/API
proof establish recovery behavior; they do not establish real financial, workbook, Render or Archive
acceptance. Issue #417 remains open for the full downstream campaign.
