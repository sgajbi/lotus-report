# Risk source qualification

Report admits calculate and rolling Risk responses through one typed boundary in
`src/app/services/risk_supportability.py`. Successful transport and usable figures do not
establish source readiness. Risk authority comes from nested `metadata.calculation_supportability`
and `metadata.source_returns_evidence`, not words found elsewhere in the response.

| Producer state | Report section posture | Captured call posture |
| --- | --- | --- |
| `ready` | `ready` | `complete` |
| `stale` | `partial` | `partial` |
| `degraded` | `partial` | `partial` |
| `empty` | `unavailable` | `unavailable` |
| `error` | `unavailable` | `error` |
| `permission_blocked` | `unavailable` | `unavailable` |
| `unsupported` | `unavailable` | `not_supported` |
| missing, malformed, mismatched or contradictory qualification | `partial`, explicitly `unknown` | `partial` |

`source_qualification` retains the producer state, safe reason, freshness bucket, echoed scope
and exact consumed source evidence. Missing, invalid, identity-mismatched or inconsistent
qualification receives a bounded Report-owned reason. Arbitrary upstream reason text is not
published. Only HTTP 2xx can admit Risk qualification; redirects and unexpected transport
statuses cannot become complete. Ordinary transport failures remain transport failures, and other service admission
is unchanged.

Consumed evidence must identify `lotus-performance`, a UUID calculation handle, `v1` contract,
SHA-256 input fingerprint and calculation hash, known freshness and strict nonnegative integer
counts. Requested points must equal returned plus missing points. Coverage accepts the exact
ratio within the producer's existing `1e-12` compatibility or its discrete eight-decimal
`round(ratio, 8)` wire value; it does not widen the tolerance or rewrite evidence. Zero requested
points require ratio `1`. A 269/270 wire ratio of `0.9962963` remains partial evidence. A claimed
ready state with stale, incomplete or unknown-freshness evidence is inconsistent and stays unknown.

Risk's public response scope echoes as-of date, reporting currency and NET/GROSS basis; Report
binds these to the actual stateful request. The current Risk wire does not echo portfolio
identity. Report retains request lineage and opaque consumed source hashes, and does not pretend
those hashes independently prove portfolio identity or a common source cut.

Per-period calculate fallback retains each period's qualification rather than treating the first
metadata block as authority for every period. A later ready period cannot erase another period's
limitation. The ordered Risk section includes point-in-time and rolling evidence: limited rolling
source evidence prevents an otherwise ready Risk section from claiming readiness. Usable source
figures remain unchanged. Captured metadata, source hashes and qualification are reused by
retained replay and rerender without fresh Risk calls. Existing `risk_posture` notes in the Render
request carry calculate and rolling limitations without a new Render contract.

Run focused controls from the `lotus-report` repository root on PowerShell or Bash:

```text
python -m pytest tests/unit/services/test_risk_source_qualification.py tests/unit/services/test_risk_qualification_retention.py
```

`tests/integration/test_risk_qualification_postgres_retention.py` uses the owning CI PostgreSQL database,
closes capture adapters and reads the retained snapshot in a separate Python process before
recreated recovery adapters replay and rerender it. Its Risk HTTP transport and Render/Archive
suppliers remain controlled. This is not a live Performance-to-Risk-to-Report-to-Render run,
service-worker restart proof, production IAM proof or certification. Full #398 acceptance requires
the separately coordinated source-pinned joined proof; Risk #377 and source completeness remain
separate. Report-wide reconciliation remains the designed unknown defined by its posture contract.
