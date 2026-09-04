# End-to-End Report Lifecycle

This page is the single governed description of how a client report moves through the Lotus
reporting capability: **lotus-report** composes authoritative truth, **lotus-render** presents
it, **lotus-archive** preserves it, **lotus-gateway** fronts ordering and retrieval,
**lotus-workbench** surfaces the workflow, and **lotus-ai** contributes review-gated narrative
through explicit contracts. Every claim below is enforced by merged code and tests; where a
capability is gated or pending, the gap is stated explicitly rather than implied away.

## Ownership boundaries

| Service | Owns | Never owns |
| --- | --- | --- |
| lotus-report | WHAT the document communicates: report families, sections, composition, lifecycle orchestration, as-of semantics, lineage, failure semantics, Report-owned custody metadata (`render_context.archive`) | Rendering layout; document storage; byte delivery to Archive (lotus-render is the one transmit authority, render#120); entitlement |
| lotus-render | HOW the document looks: templates, layout, typography, deterministic PDF production | Report facts; document retention (its store is job evidence, not documents - render#120) |
| lotus-archive | Durable document identity, retention, legal hold, purge, access audit, source events | Report composition; render execution |
| lotus-gateway | Caller entitlement, scope eligibility, ordering-options projection, document retrieval fronting | Report configuration truth (it projects report's catalogue verbatim) |
| lotus-ai | Governed AI runs, human review state, the accepted-output projection | Client-report suitability; returns/valuations/P&L/exposure/suitability/risk facts (AI is never authoritative for these) |

## Lifecycle state model

Report job statuses are the durable truth; the conceptual stages map onto them:

| Conceptual stage | Report job status | Notes |
| --- | --- | --- |
| Requested | `accepted` | Idempotent acceptance: identical resubmission returns the same job |
| Assembling | `collecting_data` | Upstream reads recorded as durable upstream-call evidence |
| Ready for render | `data_ready` | Immutable input snapshot exists; JSON-only jobs stop here as a complete outcome |
| Rendering | `rendering` | Render package built deterministically from the snapshot; submitted under `rdr_{job_id}_pdf` |
| Rendered | `completed` | Render truth persisted: template identity, `artifact_sha256`, `bounded_determinism_fingerprint`, runtime identity |
| Archiving | `archiving` | lotus-render delivered the exact bytes to Archive during the render call (render#120); Report records the custody outcome under the derived `areq_` request id |
| Available | `archived` | Reached ONLY on `archived_verified`: Archive independently verified the declared SHA-256 and holds the bytes; `archive_document_id` recorded |
| Degraded success | `completed_with_warnings` | Explicit partial outcome, never silent |
| Failed | `failed` | Bounded `failure_category` + operator-actionable message + explicit `retry_eligible` |
| Cancelled | `cancelled` | Only before render/archive side effects |

Failure transitions preserve render evidence (ledger transitions use COALESCE semantics): a job
failed after its render completed still carries the render identity, artifact hash, fingerprint,
and runtime identity - evidence is never destroyed by a failure.

## Identity and integrity chain

| Identity | Minted by | Determinism |
| --- | --- | --- |
| Idempotency key | Caller (Gateway/Workbench) | Identical retries converge on one job; this is the ONLY identity a caller supplies |
| `report_request_id` (`rrq_...`) | Report acceptance | Server-minted per request record; never reuse it as an idempotency key - that creates a NEW job instead of converging |
| `report_job_id` | Report acceptance | Fresh per job |
| `snapshot_id` + `snapshot_hash` | Capture | One immutable snapshot per job; hash pins content |
| Upstream call records | Capture recording clients | Request/response hashes, latencies, postures per source read |
| `render_job_id` | Report (`rdr_{job_id}_pdf`) | Deterministic per job; render converges identical retries via package-hash idempotency |
| `artifact_sha256` | Render | Bytes of ONE render; NOT stable across re-renders (PDF metadata differs) - never compare across renders |
| `bounded_determinism_fingerprint` | Render | Stable across re-renders of the same input **within the governed container runtime only** |
| `archive_request_id` | Derived (`areq_` from `document_reference` + `artifact_sha256`) | Any holder of the same reference and digest computes the same id - Render derives it to deliver, Report derives it to reconcile; archive converges identical redeliveries idempotently. Legacy rows keep their stored `arch_{render_job_id}` ids |
| `document_id` + checksum | Archive | Fresh per archived document; checksum verified on ingest |
| Advisor commentary `run_id` + `content_hash` | lotus-ai (accepted-output projection) | Pinned at capture; kept in snapshot, lineage, render package, and archive metadata |

## Failure semantics (all proven by tests on main)

| Failure mode | Category | Retry posture | Recovery |
| --- | --- | --- | --- |
| Source data unavailable / upstream error | `upstream_data_failed` / `timeout` | Retryable | Replay after upstream recovers |
| Requested inputs unsupported | `validation_failed` | Not retryable | Corrected new order |
| Render rejects the package (422) | `render_validation_failed` | Not retryable | Fix at source, new order |
| Same render id, different package (409) | `render_conflict` | Not retryable | Investigate identity misuse |
| Render unavailable / 5xx | `render_execution_failed` | Retryable | Replay |
| **Timeout after successful render** (terminal replay returns no artifact bytes) | `render_artifact_unrecoverable` | **Retryable** | Replay clones the retained snapshot and re-renders under a fresh render job id; content-identical by fingerprint, byte-different by design |
| Custody deadline expired, delivery may have committed (`archive_pending`) | `archive_outcome_unknown` | Retryable | The derived `areq_` id is recorded durably BEFORE the failure posture. Replay FIRST resolves it against archive: a committed document is adopted (the source job becomes archived, no second document possible), an unanswerable lookup refuses fail-closed, and only a confirmed 404 re-renders - which redelivers under the SAME derived id and converges |
| Archive refused custody with a 4xx (`archive_failed`, detail `archive_refused_4xx`, e.g. `declared_checksum_mismatch`, `artifact_identity_collision`) | `archive_handoff_failed` | Not retryable | Deterministic re-renders redeclare the same digest and re-fail identically; operator investigates the identity fault |
| Archive unreachable / 5xx refusal / contained handoff crash (`archive_failed`, other details) | `archive_handoff_failed` | Retryable | Redelivery is idempotent by the derived request id - a retry converges, never duplicates |
| Render reported no archive handoff (`archive_state` null) | `archive_handoff_not_configured` | Retryable | Configuration error: the byte relay is retired, so there is no fallback delivery. Fix lotus-render's `LOTUS_RENDER_ARCHIVE_BASE_URL` and retry; a governed document is never silently left out of custody |
| Lineage recording failed after a verified correction/replacement | (job event `job_archive_lineage_pending`, no failure category) | Self-recovering | The stored document stands - never destroyed or unarchived for pending linkage. The old->new pair is recorded through Archive's OWN lifecycle API (`/documents/{id}/correct` for rerender corrections, `/supersede` for regenerate replacements - report#266); a pending pair is re-attempted on the next correction-flow entry, and Archive replays recorded pairs idempotently |
| Legacy rows: archive validation / conflict / storage / execution failures | `archive_validation_failed` / `archive_conflict` / `archive_storage_failed` / `archive_execution_failed` | As recorded | Pre-cutover categories remain replayable under their stored `arch_` request ids (report#211/#215 resolution-first recovery) |
| Advisor brief not accepted / not found / context mismatch / disclosure impossible | Section closes, job proceeds | n/a | Reason recorded as job event + snapshot + lineage; document truthfully omits the section |
| lotus-ai transport failure or 401/403 | `upstream_data_failed` | Retryable | Fix environment (401/403 = caller registry fault), then replay |
| Duplicate submission | n/a | n/a | Idempotency keys converge at every hop (job, render, archive, replay, events) |

## Recovery commands

| Command | Eligible jobs | Guarantees |
| --- | --- | --- |
| `POST /reports/jobs/{id}/replay` | `failed` + `retry_eligible`, portfolio-review only, no archive document | New job under a replay idempotency key; for `render_artifact_unrecoverable` the retained snapshot is CLONED (upstream never recollected, clone lineage names the source snapshot; refuses fail-closed if the snapshot is purged); crash-resumable; fingerprint comparison recorded once per durable event; ONE replacement per LINEAGE per source - a novel idempotency key is refused while ANY member of the replay lineage - walked transitively from the lineage root, with creators serialized on the root row - is live, succeeded, or archive-ambiguous, so the original work can never gain two replacement documents through any branch |
| `POST /reports/jobs/{id}/rerender` | `archived` | Resolution-first (report#215): an attempt that failed on the archive stage is resolved against its own `arch_{render_job_id}` before any new attempt - a committed correction is ADOPTED as that attempt's outcome, an unanswerable lookup refuses, and only a confirmed 404 permits the fresh attempt (fresh render job id, supersession recorded). Archive-stage attempt failures are retry-eligible for every family under this guard |
| `POST /reports/jobs/{id}/regenerate` | `archived`, portfolio-review only | New job recollecting CURRENT upstream data; relationship + archive consequence recorded; other families regenerate by resubmitting their own order |

Every job-scoped read and command refuses cross-tenant, cross-region, and mismatched
booking-centre callers with the same not-found answer as an unknown id (see
[Security and Governance](Security-and-Governance)).

## Observability map

Two processes record metrics and **both must be scraped**: the API service (`/metrics`, port
8300 locally) and the job worker (its own exporter on `REPORT_JOB_WORKER_METRICS_PORT`,
default 8301). The canonical async capture/render/archive paths execute in the worker; the replay command executes in the API process.

| Signal | Where |
| --- | --- |
| Operation counts + durations per stage | `lotus_report_operations_total` / `..._duration_seconds` (worker + API) |
| Advisor commentary outcomes by bounded reason | `lotus_report_advisor_commentary_resolutions_total` - worker for the canonical async flow, API process for replay-driven captures: scrape both |
| Fingerprint comparison outcomes | `lotus_report_replay_fingerprint_comparisons_total` - **API process** (the replay command executes in the API, not the worker); point the windowed `increase(...{outcome="diverged"}[1h]) > 0` alert at the API exporter |
| Per-job truth | Status events (bounded contract, idempotent appends), diagnostics endpoint with bounded flags (`replay_fingerprint_diverged`, `snapshot_not_captured`, ...) |
| Evidence | Snapshot + upstream-call lineage endpoints; archive source events carry artifact refs including `advisor_brief_accepted_output` |

## Cross-service contract seams and drift protection

| Seam | Contract | Drift protection |
| --- | --- | --- |
| Report catalogue → Gateway → Workbench | `report-ordering-catalogue.v1`; field vocabulary incl. `text`/`conditional` | Gateway parses fail-closed (`report_catalogue_contract_invalid`); vocabulary alignment landed as gateway#690; a standing vocabulary gate is a noted follow-up |
| lotus-ai accepted output → Report | `lotus-ai.workflow_pack_run.accepted_output.advisor_brief.v1`; typed evidence refs; tones {positive, neutral, warning}; whitespace-collapsed plain prose | Report validates schema id + run id on every 200; mismatches close the section as fabricated-provenance protection |
| Report render package → Render | `report_data` carries section data incl. `advisor_commentary`; render ignores unknown keys | PDF orders REFUSE the commentary section until the render template exists - a document must never silently omit an ordered section |
| Report archive handoff → Archive | Typed metadata summaries (narrative, memo, idea pack, advisor commentary) | Archive validates fail-closed per summary; unknown keys are typed before they can be silently dropped |

## Known gaps (tracked, deliberate)

- Pre-order availability display for advisor commentary awaits the accepted-run lookup
  (lotus-ai#183); until then the section is offered only when the caller holds an accepted run id.
- The PDF path for advisor commentary is gated until lotus-render's template renders the section
  (coordinated; the gate is removed in the same change that enables it).
- Render artifact bytes exist only in the original response (render#120): recovery is proven and
  cheap, so this is an efficiency decision pending an owner call between render persistence and
  an archive staging store.
- Analytics coverage roadmap: report#209 records the grounded unconsumed upstream capabilities.
