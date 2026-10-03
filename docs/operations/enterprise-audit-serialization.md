# Enterprise Audit Serialization

The enterprise emitter and shipped `JsonFormatter` share the validation and recursive redaction
policy in `src/app/audit_logging.py`. The default logging setup uses one Python `StreamHandler`
(stderr by default); deployment collection may route that stream to a stdout-oriented collector.
Each valid enterprise event appears as `message=enterprise_audit_event`,
`logger=enterprise_readiness`, with this nested audit contract:

| Field | Contract |
| --- | --- |
| `schema_version` | `lotus-report.audit.v1` |
| `service` | `lotus-report` |
| `action` | Strict string, 1–1024 characters; `METHOD /path` or `DENY METHOD /path` |
| `actor_id`, `tenant_id`, `role`, `correlation_id` | Caller-declared strings of at most 256 characters, or explicit JSON null when absent |
| `timestamp_utc` | Timezone-aware event time normalized to UTC, distinct from the formatter's outer `timestamp` |
| `policy_version` | Strict string, 1–64 characters |
| `metadata` | JSON object; finite JSON values, at most 8192 UTF-8 bytes after redaction and standard JSON encoding |

The typed envelope forbids unknown top-level fields. Write responses reaching the route return
retain `metadata.status_code`, including successful admission/replay, conflicts and validation
failures. Authorization denials retain `status_code=403` and the bounded policy reason.
Enabled GET/HEAD audit adds `access_type=read`; `ENTERPRISE_AUDIT_READS=false` suppresses ordinary
read events, while denied reads still emit denial events. Existing early payload-length/body-size
refusals and exceptions that do not return a route response are outside this middleware emission
coverage. This change does not add audit events to those paths.

Identity reflects received caller headers. It does not invent a tenant or actor for absent headers,
nor replace a missing caller correlation with an internally generated access-log identifier.
Caller declaration is not independently authenticated identity. Job-worker attribution and ambiguous
header admission remain separate controls. Outer correlation/request/trace fields retain the
observability context; audit correlation retains the emitter's caller fact. Neither establishes
exported traces.

Before any logging sink receives the `LogRecord`, the emitter validates and recursively redacts
metadata. Case-insensitive `password`, `secret`, `token`, `authorization`, `ssn`, `account_number`
and `client_email` keys become `***REDACTED***` in nested objects and arrays (including tuple
inputs normalized to arrays). Input metadata is not mutated. The formatter validates again;
malformed envelopes, non-finite or unsupported values and oversized metadata emit only the fixed
`audit_serialization_error=invalid_audit_envelope`, without the raw envelope or validation details.
Generic `extra_fields` are redacted and cannot replace timestamp, level, service, environment,
logger, message, context IDs, audit or the fixed serialization-error field.

Regression evidence uses the actual emitter/formatter together, including nested credentials,
malformed/non-JSON/oversized metadata and protected-field collisions. Registered loopback HTTP
captures the existing shipped handler's JSON output with actual durable SQLite admission:
12 requests reconcile to 11 audit events, covering admission, replay, conflict, failed request,
write/read denial, enabled reads and disabled reads. Reopening the ledger proves one original job
and no additional admission through refused requests. Synthetic credentials are excluded from
retained request evidence and do not appear in log JSON.

This establishes serialization and the bounded request-to-audit relationship. It does not establish
immutable storage, delivery acknowledgement, collector monitoring, retention, production IAM,
regulatory certification or PostgreSQL recovery. Added sinks must consume the admitted envelope;
each sink's delivery and retention policy requires separate evidence.
