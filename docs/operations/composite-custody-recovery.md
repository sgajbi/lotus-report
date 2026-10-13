# Initial composite custody recovery

## Inactive retained-offer candidate (#446)

The new `retained-render-offer.v1` cohort is **disabled by default**. Report ledger
factories supply no `CustodyBindings`; only injected server admission may mark a
new job. Caller fields cannot opt in, and existing NULL-policy jobs retain their
existing behavior. This source candidate is not an activated recovery service.
The replay command described below does not resume a required-cohort job.

Before the first Render POST, Report commits the complete pre-render JSON body as
compact UTF-8 with nonfinite numbers rejected. Bytes, SHA-256, encoding version,
size, generation and original Render identity live on the existing `report_job`.
Dispatch reads committed bytes, including after an ambiguous commit or restart;
ordinary job reads expose metadata only. Known-ID recovery never reacquires sources
or recomposes a replacement package. Report adds no Archive POST sender.

The existing work row carries recovery. Unresolved custody at expiry, exhaustion,
completion, cancellation or failure parks that row as failed while preserving the
nonterminal job, bytes, identities and counters. It does not emit `job_failed`.
Internal authorized resume uses the parked epoch and offer digest, the same job and
work IDs, and a new finite recovery window. Refusal cannot implicitly reset.
An `archived_verified` response alone neither verifies custody nor releases bytes.
Even independent final verification retains the body; no deletion or transfer exists.

| Bound | Candidate ceiling |
| --- | --- |
| Complete retained body | 8 MiB |
| Aggregate / per tenant retained bodies | 128 MiB / 32 MiB |
| Decoded HTTP response / diagnostic | 128 KiB / 1,024 bytes |
| Mutations | One initial and three recovery invocations per window |
| Status reads / recovery window | 12 / 24 hours |
| Recovery gaps before attempts one / two / three | 60 / 300 / 900 seconds plus 0–10 seconds jitter |
| Item / pass targets / sequential item count | 60 seconds / 120 seconds / at most five; DB deadline prerequisite below |
| Required lease | At least 120 seconds; worker default 300 seconds |
| Mutation / status transport deadline | 45 seconds / 10 seconds, one attempt |

Reservations commit before transport and are never refunded after unknown outcomes.
Initial reservation schedules the first recovery; each recovery schedules its next
permitted attempt. Validated future owner due times persist for both wait and retry,
survive restart, align work deferral and prevent early polls from consuming the budget.
SQLite uses `BEGIN IMMEDIATE` before facts and quota; PostgreSQL source locks work,
then job, then the single quota advisory lock, checks database time after waits, and
sweeps one pair per transaction with `SKIP LOCKED`. Neither cap permits eviction.

One worker allowance starts before claim/sweep and covers execution and settlement;
execution receives no fresh allowance after claim. At expiry, the worker starts no
new settlement transaction and reports the work outcome as unknown for existing
expiry recovery. A committed verified winner remains authoritative. These checks
and async cancellation **do not bound a synchronous database call**. An isolated
SQLite writer-lock test demonstrates that claim can outlast the scaled allowance.
The PostgreSQL provider now has one fixed inventory for legacy and private strict
leases. Strict handles bind provider, generation and owner thread; their terminal
return publishes directly through the owned slot without taking the shared provider
condition. Unknown physical close stays quarantined and charged, with no automatic
retry or replacement. Raw legacy `release(connection)` retains its valid-caller
precondition and does not promise stale-generation safety.

Strict construction requires a lifecycle owner and an absolute monotonic shutdown
deadline supplied before enablement. No production factory enables strict mode.
Strict admission only uses already-open idle connections, and discard is the default;
an internal caller may mark return reusable only after establishing clean transaction
state. Foreground drain seals admission and uses the original shutdown bound, never
an item budget or fresh grace. Incomplete drain raises `PostgresProviderDrainIncomplete`
and the cached provider remains retained. Quarantine requires lifecycle-owner recovery;
the provider does not silently release capacity or claim a completed shutdown.

API teardown forwards `application.state.postgres_shutdown_deadline` when an owner
supplies it. The worker wrapper accepts `shutdown_deadline` and cleans up on normal
exit, failure and cancellation, including construction failure. Both retain legacy
cleanup defaults when no owner deadline is supplied. Legacy close drains idle slots;
an outstanding legacy owner closes its connection on subsequent return.

These are ownership and lifecycle checks. Native connect, SQL, commit, rollback,
cancel and close remain synchronous; deadline checks detect late return and cannot
preempt those calls. Separate pool/connect/statement limits do not establish an
absolute deadline through transaction commit, rollback and connection cleanup.
**Finding 1 remains OPEN/P1 and the cohort remains disabled.**
Strict 60/120-second wall-clock qualification therefore remains blocked on a
provider deadline contract and matching SQLite lock/transaction handling; no global
timeout change or detached database thread is supplied by this candidate.

Activation remains blocked on Platform#775 current authenticated action/resource,
Composite, tenant, region, audience and revocation bindings; agreed Render#352 and
Archive#196 recovery semantics; actual PostgreSQL qualification; installed provider
and Docker cancellation/resource proof; and Root-owned combined source-off and
capacity evidence. The 2 GiB deployment/reserve prerequisite is unproved. Header
presence, institution names and hosting do not confer authorization. No production
binding, public resume endpoint, dispatcher activation or new custody cleanup path is supplied.

Focused isolated proof uses real file SQLite process death and contention with
synthetic authority and HTTP peers. From the `lotus-report` repository root with its
pinned development environment active, run this command in Bash or PowerShell:

```text
python -m pytest tests/unit/reporting_jobs/test_retained_custody.py tests/unit/clients/test_custody_transport.py -q
```

It establishes neither native PostgreSQL behavior nor live authority/provider proof.

Provider lifecycle proof is also isolated and uses controlled connection doubles.
From the same repository root and pinned development environment, in Bash or PowerShell:

```text
python -m pytest tests/unit/test_postgres_connection_provider.py tests/unit/test_postgres_adapter_ownership.py tests/unit/test_postgres_shutdown_lifecycle.py tests/unit/reporting_jobs/test_report_job_process.py -q
```

Actual Psycopg/libpq cancellation, physical close bounds and resource limits, plus
PostgreSQL/Docker qualification, remain Root-owned activation prerequisites.

## Existing legacy replay workflow

For an accepted `composite_review.v8` XLSX job whose initial Archive handoff failed,
use the existing `POST /reports/jobs/{job_id}/replay` operator command with trusted caller
context, a bounded reason and a stable `Idempotency-Key`. This is failed-work recovery;
presentation changes to already archived jobs continue through the rerender command.

Start with the [decision table](#recovery-decision), then the
[synthetic request](#synthetic-replay-request). Check the
[custody and authority requirements](#custody-and-authority) before retrying.

## Recovery decision

| Source outcome | Recovery behavior |
| --- | --- |
| Pending deadline or ambiguous failed handoff, recorded Archive request | Resolve that exact request before replacement work |
| Matching committed document | Adopt the original document; source becomes archived; replay returns 409 because replacement is unnecessary |
| HTTP 404 with Archive's `error.code=document_not_found` | Clone retained snapshot facts and render one replacement through Render's existing handoff |
| Unavailable lookup, malformed/mismatched identity, missing retained evidence | Refuse with 409; no replacement render or source acquisition |
| Definitive Archive 4xx refusal | Source remains non-retryable pending operator action |
| Missing, malformed or unrecognized Render custody state | Preserve any returned request ID and resolve it first; this does not prove that delivery was never attempted |
| Unresolved custody without an original request ID | Non-retryable; operator reconciliation required, with no identity inferred from unknown-state artifact bytes |

## Synthetic replay request

Save the following as `custody-replay.curl` in any working directory. All identifiers are
synthetic: replace the job ID and caller context with an authorized failed job in the same
tenant, region and booking centre. These headers represent trusted context forwarded by the
authenticated boundary; writing headers does not grant authority. Role and service identity below
are synthetic. Use the role and principal forwarded by your provisioned authenticated boundary;
an authorized `Authorization` header may replace `X-Service-Identity`. Report's header-presence
gate is not credential verification or IAM provisioning. If the source has a booking
centre, also supply its exact `X-Booking-Center-Code`.

```curl-config
url = "http://report.dev.lotus/reports/jobs/rjob_synthetic_failed/replay"
request = "POST"
header = "Content-Type: application/json"
header = "X-Actor-Id: recovery-operator"
header = "X-Caller-Application: lotus-gateway"
header = "X-Role: SUPPORT"
header = "X-Service-Identity: synthetic-trusted-gateway"
header = "X-Tenant-Id: tenant-a"
header = "X-Region: APAC"
header = "X-Correlation-ID: corr-recovery"
header = "X-Trace-ID: trace-recovery"
header = "Idempotency-Key: custody-replay-synthetic-v1"
data = "{\"reason\":\"Recover custody.\"}"
```

From that directory, run `curl --include --config custody-replay.curl` in Bash, or
`curl.exe --include --config custody-replay.curl` in PowerShell. This is the direct Report
route; it requires the governed runtime serving the canonical origin, trusted caller context,
and an existing eligible job. Authorization-enabled environments also require nonempty role and
principal headers; forward any `X-Capabilities` required by the deployed policy as well.
Missing required context returns `403` before recovery. Reuse the same
key and body for a retry; a new key must not bypass a live or custody-ambiguous descendant.

| Observed result | Operator action |
| --- | --- |
| `202` replacement | Record `replayed_report_job_id`; inspect its job status and archive outcome. Acceptance alone is not delivery proof. Same-key retries converge on that job. |
| `409` after committed adoption | Re-read the original job: it is archived with the adopted document. No replacement is needed. |
| `409` refusal | Reconcile retained evidence or the original custody request; do not recollect current sources or rotate keys to bypass the refusal. |
| `404` | Check the job identifier and caller segregation; unknown and foreign jobs share the same response. |

The registered-route [documentation test](../../tests/unit/composite_reporting/test_custody_recovery_documentation.py)
executes these headers/body with `ENTERPRISE_ENFORCE_AUTHZ=true` for replacement, adoption,
refusal and foreign-tenant cases, plus missing/empty-role and missing-principal denials before
recovery side effects.
It changes only the synthetic job ID to the fixture's opaque ID and uses controlled downstream
ports. From the repository root, run
`python -m pytest tests/unit/composite_reporting/test_custody_recovery_documentation.py -q`
in Bash or PowerShell with the repository's development environment active.

## Custody and authority

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

## Evidence boundary

This scope covers v8 XLSX initial custody only. Controlled transport tests and owned PostgreSQL/API
proof establish recovery behavior; they do not establish real financial, workbook, Render or Archive
acceptance. Issue #417 remains open for the full downstream campaign.
