# Direct-Service Request Header Admission

The enterprise middleware applies one raw, case-insensitive header cardinality policy before
dictionary/scalar projection, authorization, body consumption or route dependencies. All Report
commands and job read/control routes use this boundary. Internal workers and already persisted
caller contexts retain their own application admission controls.

| Cardinality | Header names |
| --- | --- |
| Exactly one line when supplied | `Authorization`, `X-Service-Identity`, `X-Actor-Id`, `X-Caller-Application`, `X-Tenant-Id`, `X-Region`, `X-Booking-Center-Code`, `X-Role`, `X-Correlation-Id`, `X-Trace-Id`, `X-Request-Id`, `traceparent`, `Idempotency-Key` |
| Deliberate list semantics | `X-Capabilities`: combine all raw lines, then apply existing comma-delimited exact capability membership |

Every repeated scalar is refused, including identical values, blank neighbors, reversed order and
mixed casing. The policy does not select a first scalar or silently canonicalize duplicates.
It does not impose a new presence requirement: existing authorization, missing-context,
idempotency and tenant/region checks still decide whether a singleton is required or usable.
Separate `traceparent` and `X-Trace-Id` lines retain their existing precedence; each name itself
must be singular. A comma inside one scalar line remains one opaque scalar value, not a capability
list. The policy concerns the raw collection Report actually receives, not reconstruction of
headers already rewritten by a proxy.

The stable response is HTTP400, with lower-case header names sorted and no supplied values:

```json
{"detail":{"code":"ambiguous_request_headers","headers":["idempotency-key","x-tenant-id"]}}
```

Rejection occurs before route handling and its durable mutations. The audit is `DENY METHOD /path`,
with `status_code=400`, `reason=ambiguous_request_headers` and the refused names. Audit actor,
tenant, role and correlation come only from unambiguous singleton lines; a duplicated field is
explicit null rather than false attribution to its first value. The shared governed audit envelope
and redaction policy apply. Authorization denials remain403; missing idempotency remains400;
same-key replay and changed-request409 conflict remain unchanged.

Regression proof sends real raw HTTP lines through the shipped listener and default PostgreSQL
providers, without dependency overrides. It covers all 13 scalar names with equal/different values
in both orders, the exact two-key reversal (neither key becomes a row), capability-list orders,
job reads/control routes and ordinary missing inputs/replay/conflict/tenant/region fences.
Request/job/status-event/work-item counts and SHA256 fingerprints of every owned row are checked
before and after refusals, replays and read controls. One valid request
remains one accepted job with pending, unleased work; its tenant, actor, caller, region and
correlation match the admitted singleton facts. Test cleanup removes only its known owned rows;
the listener is closed and probe refusal is verified. Retained wire headers preserve line names
and order, with credential values redacted.

An OpenAPI-derived unit inventory requires every declared scalar authority/replay header to have
an explicit policy; `X-Capabilities` is the named list exception. New identity/replay headers require
classification in `src/app/request_header_admission.py`, native wire/no-effect controls and an
update to this guidance. Do not add ad hoc first-value handling in routers or caller-context code.

This establishes direct-service cardinality and PostgreSQL admission behavior. Gateway forwarding,
proxy rewriting and authenticated production IAM need their own evidence. It does not establish
source calculations, worker execution, Render/Archive custody, exported traces or canonical UI.
