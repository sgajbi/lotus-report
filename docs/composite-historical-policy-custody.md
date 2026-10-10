# Historical monthly policy custody (v7)

Report adds `eligibility_selection.selection_version: "v3"` within the existing
composite-review intake, worker, immutable snapshot and retained-package lifecycle.
It selects Manage policy proposal/approval v2 and ordinary monthly proposal/approval/
receipt v3 or source-correction v4. Definitions retain their independent v1/v2 axis.
Untagged selections and selection v2 retain frozen v4/v6 behavior and fingerprints.
The output is `composite_review.v7`, template `composite-review/v7`, inside
`render_package.v1`. It remains `CONTROLLED_HISTORICAL_POLICY_EVIDENCE_REPLAY` /
`NOT_ATTESTED`; full #417 remains open.

## Admission and release boundary

`LOTUS_COMPOSITE_HISTORICAL_POLICY_ENABLED` defaults to false. Disabled capture
refuses with `COMPOSITE_HISTORICAL_POLICY_RELEASE_NOT_ADMITTED` before source I/O.
Enablement requires the producer's committed qualified main and normal Report,
Render and Archive source, PR, main and wiki qualification. Exact current template
supportability remains necessary for XLSX. Controlled fixture coverage does not
constitute deployment qualification or authenticated institutional authority.

Report consumes configured-identity producer responses and checks complete schema,
hash, operation intent, actor, version, tenant, scope, date, currency and retained
lineage bindings. Report preserves raw-original base64 and credentials completely.
Manage owns original-format cryptographic verification, trust pins, revocation and
current admission. Report checks recorded proof bindings and credential format;
it does not perform cryptographic verification or grant fresh authorization from
caller-supplied proofs. Fixed headers and reader enrollment do not establish bank
provenance. No new cryptography dependency, trust service or execution runtime is
introduced. Native historical institutional authority, production source-format
adapter and service-principal qualification remain unavailable.

Original proposal/approval events precede the target month; their original bytes
and signing contract remain distinct from later normalized policy admission and
operation proofs. Recorded proof clocks follow Manage's existing contract:
`checked_at <= requested_at <= admitted_at < expires_at`, with expiry at most five
minutes after checked. Real network admission may follow the exact operation request
clock. Every clock requires a timezone; admission at expiry, reversed clocks and
overlong windows refuse. Retained proofs are checked against their recorded window,
not the current wall clock, so later replay does not grant fresh authority or reject
otherwise valid historical evidence merely because that window has elapsed.

Every correction retains its nearest predecessor first, ending
at exactly one ordinary v3 root, with no cycles or version downgrade. The existing
same-policy/population ordinary-month correction rules and canonical publication,
membership, universe and projection-parent checks apply.

## Projection and capacity

The existing eight eligibility tables are followed by `Amendments` and
`PolicyAdmission`. Both retain exact source scalar values as text and complete
RFC6901 pointers. Traverse months in selection order, selected proposal first,
then nearest-to-oldest retained receipt. Within each proposal, emit every
`policy_approval` scalar with role `POLICY_ADMISSION`, then every
`operation_verification` scalar with role `EVALUATION_PROPOSAL`; published and
retained receipts also emit every approval operation proof scalar with role
`EVALUATION_APPROVAL`. Object keys sort lexically, arrays retain order, and null
leaves remain present. Policy `scope/run_id` null is
`NOT_APPLICABLE` / `POLICY_RUN_NOT_APPLICABLE`.

PolicyAdmission columns are `month`, `evidence_role`, `value`; row IDs are
`m{month_index}:p{global_table_row_ordinal}`. Amendment columns are `month`, `value`;
IDs use `m{month_index}:a{global_table_row_ordinal}`. Ordinary roots have an explicit
`/report_facts/no_amendment` placeholder with `NOT_APPLICABLE` /
`ORDINARY_ROOT_NO_AMENDMENT`. The exact custody boundary is also included in
disclosures and the workbook Identity `calculation_boundary` field.

Keep all existing aggregate, cell, sheet and package preflight caps. Each table
has at most 10,000 rows, selections at most 120 months, and correction lineage at
most 31 receipts. Oversize evidence refuses; proofs and raw bytes are never
shortened. No TWR, MWR, dispersion, contribution or model-fee calculation is added.

## Controlled source evidence and validation

Pinned schemas/examples come from Manage schema packet manifest
`42f303700e1699774df2a8babd34eb32d79aee3a261ddb7f7b6ceed141834a2e`;
complete graphs use manifest
`4b91c97835bcb2b00d095c0ce02009d9b519e81847966ac07e2557d64d644c57`.
The unchanged schema packet and graph payloads are now published in signed Manage
candidate `09bb66341b7aebc84922d20e4694d604bdd727a6`,
[PR 799](https://github.com/sgajbi/lotus-manage/pull/799), based on
`9224d85664fb07e8074ef21681b88449d05193e8`. Candidate publication is not qualified
main or release admission. The later graph manifest
`a64a6d19136391559b9802993170b89d1db295fe79042e23981f2f92a24db8ca`
only records portable source-code provenance using UTF-8/LF; all twelve graph
payload bytes match the retained first manifest. Original artifact bytes,
base64 and signing contracts are never normalized.
Graphs were exported through existing producer fixtures, memory UOW and registered
TestClient routes with a generator-only clock. They cover both definition profiles,
ordinary root followed by two corrections, and evaluated/published outcomes.

The separate two-month manifest
`0ff8e4ff783412abebff1dd329e1d2e1dd24a4afe4bbc354fc3df90d3d109d6f`
retains September published plus October evaluated-only/published evidence from
separately created original policy artifacts. Its fictional November 2/3 operation
clocks are controlled test inputs. Tests admit the real linked products and verify
global provenance/amendment row ordinals across months and retained JSON reordering.
The [frozen exporter and provenance](https://github.com/sgajbi/lotus-manage/issues/778#issuecomment-6091776010)
reproduce these products through existing services; no September artifact is
relabelled or re-signed to manufacture October evidence.

From the Report checkout, PowerShell or Bash:

```text
python -m pytest tests/unit/composite_reporting/test_historical_policy.py tests/unit/composite_reporting/test_monthly_amendment.py
```

The focused recorded-window regression runs from the same checkout:

```text
python -m pytest tests/unit/composite_reporting/test_historical_proof_windows.py
```

`tests/fixtures/composite-historical-policy/native-admission.json` retains unchanged
public signed products from Manage's native HTTPS/provider and PostgreSQL proof on
candidate `010ea553f40f5b15c4588bccb1ba52ac8c2b7c3b`, qualified main
`211e50ded2f4a1d41b8e7fec9ef89d6ac727169f`. Its packet SHA-256 is
`10de3e65bc04e4995fc98bf7036659b963ddf91eaebfb4b61d866683a52b5b17`;
the fixture records each original custody file's hash and
[producer evidence](https://github.com/sgajbi/lotus-manage/issues/778#issuecomment-6093446571).
Both definition profiles retain ordinary v3 and correction v4 receipts and all
twelve distinct operation proofs, including real positive admission delay. Unit
tests use Report's existing schema, policy, operation and nested hash admission.
Unsigned rehashed clones separately exercise structural timing refusals; they do
not establish signature validity or source authority. This is compatibility proof
with controlled synthetic facts, not a joined Manage-to-Report runtime campaign,
institutional authentication or financial validation.

Generated `composite_historical_report_schema()` namespaces full producer schemas
without changing their contents or the frozen v4/v6 schemas. Runtime distributions
include the pinned JSON schemas as package data. Controlled package/snapshot-record
examples are transport fixtures; they do not claim durable PostgreSQL or joined
HTTP execution. Existing R7 campaign evidence remains immutable and is not repeated.
