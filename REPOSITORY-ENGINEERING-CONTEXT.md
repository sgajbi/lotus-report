# Repository Engineering Context

Operating map for `lotus-report`. It answers what this service owns, what it must never own, how
a report is produced, and what is true right now.

**This is not a changelog.** Delivery chronology and RFC-slice narration live in
`docs/architecture/CODEBASE-REVIEW-LEDGER.md`, the RFCs, the wiki, and GitHub issues. If a fact
here is only interesting because of when it shipped, it belongs there instead.

---

## Repository Role

`lotus-report` composes governed client reporting from evidence owned by other services. It decides
which reports exist, what they mean, and what evidence may enter them. It does not compute
AUTHORITATIVE financial values - valuation, performance, risk and contribution are owned upstream
and Report may only echo them - though it does derive presentation aggregates from figures it was
given, such as allocation weights in `AggregationService` and the presented total and unexplained
residual in `build_contribution_ranking`. It renders no pages, generates no narrative and stores no
durable documents. Those boundaries are enumerated in
[What Report Must Never Own](#what-report-must-never-own) and are load-bearing rather than
aspirational.

## Current-State Summary

The reporting lifecycle is production-shaped in source and proof: durable job lifecycle, immutable
evidence snapshots, typed status events, and governed render and archive handoffs, all proven
against real PostgreSQL. That is a statement about merged source and its test evidence, not about
any deployment — deployment, cutover and operator acceptance are separate boundaries and several
are explicitly open (#326 intake cutover, #373 audit credential). The
governing workstream and its remaining dependency are stated in
[Active Priorities](#active-priorities); anything genuinely blocked is in
[Known Blockers](#known-blockers). Where a fact is not yet proven, this document says so rather
than rounding up - proof boundaries are stated with their doubles named.

## Business And Domain Responsibility

`lotus-report` is the **governed report composition layer**: it assembles authoritative Lotus
truth into coherent, explainable, reproducible client and advisor communication.

1. **Report families and their contracts** — which report exists, which sections it has, what a
   section means, and what a caller may order (`report_ordering_catalogue/`).
2. **Admissibility** — whether a piece of upstream evidence belongs in *this* report: matching
   portfolio, period, as-of date, currency, benchmark and tenant.
3. **The immutable evidence snapshot** — one durable capture per job, with append-only upstream
   call lineage, so a document can be reproduced and explained after the fact.
4. **The reporting semantic model** — typed blocks describing *what the report means*
   (`allocation_presentation`, `contribution_ranking`, …), handed to Render as business meaning
   rather than layout.
5. **Job lifecycle** — durable jobs, work leasing, replay, rerender, regenerate, and the
   fail-closed posture around every uncertain outcome.
6. **Handoff identity** — render job identity, archive request identity, and the
   one-document-per-lineage guarantee.

## What Report Must Never Own

1. **Financial calculation.** Valuation, performance, risk and contribution are computed by
   `lotus-core`, `lotus-performance` and `lotus-risk`. Report composes; it never recomputes,
   corrects, or infers a number an authoritative service owns.
2. **Presentation.** No Typst, coordinates, chart geometry, styling, or page decisions cross into
   Report. Render decides how a supported semantic block appears.
3. **AI narrative.** `lotus-ai` owns generation, the exact accepted text, and review evidence.
   Report decides only whether an accepted brief is *admissible here* — never rewriting,
   summarizing or "improving" it.
4. **Durable document storage.** `lotus-archive` owns the archived artifact and its metadata.
5. **Tenant ownership truth.** The source owns which tenant owns a portfolio. Report verifies; it
   never manufactures attribution from its own configuration.

## Architecture And Module Map

FastAPI service plus a separate `lotus-report-job-worker`, backed by PostgreSQL.

| Area | Responsibility |
|---|---|
| `src/app/routers/`, `src/app/report_ordering_catalogue/` | API surface and the ordering catalogue |
| `src/app/services/reporting_read_service.py` | upstream reads composed into the snapshot payload |
| `src/app/reporting_lineage/` | immutable snapshot capture, upstream-call lineage, admissibility |
| `src/app/reporting_jobs/` | durable job ledger, work queue, leasing, lifecycle transitions |
| `src/app/reporting_render/` | render-package assembly and the semantic model sent to Render |
| `src/app/report_batch_orchestrator/` | batches, schedules, selector materialization |
| `src/app/clients/` | typed upstream clients (core, performance, risk, render, archive, ai) |
| `scripts/`, `contracts/`, `wiki/` | governance gates, domain-product declarations, operator docs |

## Runtime And Integration Boundaries

Not every arrow below is a service boundary. `order accepted -> capture` and `capture -> compose`
are internal workflow steps - service-local, but NOT atomic: capture commits the snapshot and its
upstream calls through the snapshot store, then marks the job `data_ready` through the job ledger
as a separate operation, so a failure is observable between them and recovery must expect that
state rather than assume it cannot exist. The crossings are the authoritative reads performed
during capture, and the Render, Archive and consumer handoffs. At those crossings, distinguish the
kinds of identity. Report creates `render_job_id`, the one request identity it owns. Everything
else is downstream-owned: Render is the single archive transmit and identity authority
(render#258), so `archive_request_id` is Render's - Report records it verbatim, and the local
derivation RECONSTRUCTS the same id only as a rollout fallback for responses predating the field,
guarded by a cross-repo parity test and deleted once that fallback dies. Outcome identities -
archived document id, artifact SHA - are issued downstream and only echoed. Reconstructing an
identity is not owning it: Report must never mint an archive identity independently, nor submit a
document or artifact to Archive for CREATION - that is Render's transmit authority, and removing
it is what the render#120 cutover did. Report does call Archive directly, by design, for the
lifecycle intent it owns: `record_archive_lineage` posts supersede and correct transitions through
`ArchiveClient.record_lifecycle_transition` (report#266), because Report owns the
correction/replacement intent while Archive owns the durable document lifecycle. Reconciliation
lookups and transition calls are likewise Report's to make.

### The report lifecycle

```
order accepted (durable job, idempotent)
  -> capture: authoritative reads -> IMMUTABLE SNAPSHOT + upstream-call lineage
  -> compose: snapshot -> governed semantic model (report_data)
  -> render:  lotus-render -> exact artifact (render job identity, artifact SHA)
  -> archive: lotus-archive -> durable document (archive request identity)
  -> consume: Workbench / client
```

Recovery paths: **replay** (re-run a failed job), **rerender** (new artifact from the same
snapshot), **regenerate** (new capture). Each resolves an ambiguous prior outcome *before* acting.

### Major contracts

- **Upstream (read):** `lotus-core` portfolio summary and asset allocation; `lotus-performance`
  workspace summary and contribution; `lotus-risk` analytics; `lotus-ai` accepted-output
  projection and latest-accepted lookup.
- **Downstream (write):** `lotus-render` render package; `lotus-archive` document handoff.
- **Consumer-facing:** the report ordering catalogue, report job APIs, job lineage, and the
  advisor-commentary availability seam consumed by `lotus-gateway`. The Idea evidence-pack
  boundary also exposes a tenant-scoped, exact-identity recovery receipt with a Report-owned
  lifecycle version. Consumers can distinguish unchanged replay from later Report progress or
  correction without issuing another materialization command.
- Repo-native domain-data-product declarations live in `contracts/`; `lotus-performance` and
  `lotus-risk` remain **watchlisted** consumers, so analytics-enriched evidence must not publish
  complete, unblocked trust telemetry.

## Known Constraints And Implementation Notes

1. **Explicit unavailable beats plausible but wrong.** Every absence has a bounded reason code;
   nothing is defaulted into looking complete.
2. **Posture is stated, never inferred.** `ready` / `empty` / `unavailable` are different claims:
   *empty* is a fact about the portfolio and is drawn, *unavailable* is a fact about the data and
   is said. A consumer must never infer meaning from an empty list or a present key.
3. **A transport failure is not a downstream failure.** Timeout ≠ absence; render completion ≠
   archived; a retry must never mint a duplicate client document.
4. **Uncertain outcomes resolve before retrying**, and one lineage yields at most one archived
   document. Idea materialization recovery reads the existing Report request by tenant and
   idempotency key, verifies the complete persisted Idea/portfolio identity, and never resubmits.
   Its `source_event_version` is the count of committed append-only Report lifecycle events; status
   and version are read in one database snapshot, and only a larger version is newer owner truth.
5. **The snapshot is immutable and decides.** What a document presented is answered by its
   snapshot, not by replaying today's policy against an old order. A capture commits the snapshot
   and its **upstream-call** rows in one transaction, so `data_ready` means the evidence *and* its
   lineage are durable; a snapshot whose declared calls are missing is `data_incomplete` and
   resumes rather than proceeding — snapshot presence alone never proves a complete capture.
6. **Archive identity comes from the archive.** A batch or job carries a
   source-owned archive document id only after `lotus-archive` confirms the document is
   `archived`; the `archive_document_id` is never inferred from batch or job status, and
   corrections and replacements carry their own identity rather than overwriting one.
7. **Attribution requires evidence.** A tenant stamp from configuration is not proof of ownership.
8. **One fact, one name.** When two surfaces answer the same question — the pre-order availability
   check and the capture both explain why a section is absent — they resolve through one shared
   vocabulary rather than each holding a copy. Two copies are two chances to disagree, and the
   disagreement is invisible from inside either one.
9. **An optional section may fail without failing the report** — but a section the order promised
   is never silently omitted.
10. **Where a client carries the admitted tenant, it takes it as a REQUIRED keyword argument.**
    Not defaulted: a default preserves the gap at call sites nobody found. Making the parameter
    required is what enumerated `core_query_client`'s 20 call sites, five of them
    recording-decorator overrides that a default would have left silently dropping the value.
    Absence is transmitted as an empty header rather than omitted, so a regression that stops
    threading the value looks different from a legitimately tenantless call.

    **Coverage is complete on every tenant-owned method (#375), and the completeness is pinned by
    tests rather than by this table.** Measured on this tree by counting `X-Tenant-Id` sends per
    client:

    | client | methods | sends the tenant |
    |---|---|---|
    | `core_query_client` | 6 | yes (#365) |
    | `performance_client` | 3 | yes (#361) |
    | `ai_client` | 2 | yes |
    | `archive_client` | 2 | yes |
    | `risk_client` | 4 | yes (#375) |
    | `render_client` | 5 | 3 tenant-owned yes; 2 global-by-design carry no tenant claim (#375) |

    `render_client`'s template projection and service metadata are global publication authority —
    a tenant header there, even blank, would state a scope the resource does not have.
    `tests/unit/test_risk_render_tenant_propagation.py` pins the wire behavior for both clients
    AND the partition itself: a new risk or render method fails that file until it is covered or
    classified, and `tests/unit/test_core_tenant_propagation.py` does the same for core. Sending
    is the Report-owned half only: lotus-risk admits and refuses per its receiving contract
    (risk#297 — stateful modes require the tenant once merged), and lotus-render admission lands
    under its admit-if-present rollout recorded on #375; a header a receiver ignores is inert,
    which is why the receiving halves stay owned by those services.
11. **A refusing upstream is reported, never substituted.** Collapsing a `status >= 400` into an
    empty payload and then defaulting the missing values produced `market_value_base=1250000` for
    upstream 401, 403, 404 and 503 alike. A source that did not answer is either a refusal the
    caller sees, or an entry in `unavailable_sources` naming the service, endpoint, status and
    reason — `no_response`, `pending` or `incomplete_payload`. A metric absent from a response
    while its source is listed there **was not measured**, which is the distinction a substituted
    zero destroys: `position_count = 0` is a fact about an empty portfolio.
12. **A tenant recovered from stored JSON must be a JSON string.** `TRIM(json_extract(...))` and
    `->>` both render any type as text, so `123`, `true`, `{}` and `[]` became the tenants `'123'`,
    `'1'`, `'{}'` and `'[]'`. An invented owner is worse than an absent one — `true` becoming `'1'`
    can collide with a real tenant named `1`, and afterwards nothing separates it from a genuine
    attribution. Classify malformed JSON, wrong type and absent separately; `json_valid` is
    evaluated first so malformed rows leave the set before any accessor touches them.
13. **The runner keeps an applied-migration ledger; startup replay is retired (#376).**
    `apply_report_schema_migrations` records each file in `report_schema_migration` and executes
    only unrecorded files, whole run and ledger rows in one caller-owned transaction serialized by
    a transaction-scoped advisory lock. A current-schema restart therefore performs no migration
    DDL at all — proven on populated PostgreSQL by an empty applied set and an unchanged
    `relfilenode` for every index (`tests/integration/test_migration_ledger_convergence.py`),
    which retires the per-boot CHECK replacement in historical 002/005/006/007/013/016 and 025's
    repeated `SET NOT NULL`; their DDL now executes once per database. A database migrated before
    the ledger existed converges through one bridge replay — exactly the retired startup
    behavior — and 026's internal `pg_constraint` guard is what keeps that single replay from
    rebuilding the identity index. An interrupted run rolls back completely and retries; a pending
    file sorting below recorded history is refused fail-closed
    (`report_schema_migration_out_of_order`); recorded names unknown to an older binary are
    tolerated, which is the forward-only additive promise. Fresh install and the supported legacy
    baseline are proven to converge to one schema fingerprint. The runner's splitter is quote- and
    dollar-quote aware, so guarded `DO` blocks are safe; it was not always, and migrations 025 and
    026 were shaped around a naive `schema.split(";")`.

## Active Priorities

1. **#283 — canonical report revision and evidence identity v2 (P0, governing).**
   Make every report explainable through one chain: admitted tenant → accepted
   request → source revisions → immutable snapshot → accepted document contract
   → render execution → archived artifact. Landed so far: the identity seam
   (`src/app/reporting_identity/`, fail-closed and mutation-stable), truthful
   evidence claims (admitted tenant, `evidence_posture`, unknown
   reconciliation — `src/app/services/review_evidence.py`), replay inheriting
   the accepted template contract, business-cycle-only batch identity, and
   revision minting at capture: every successful capture derives
   `report_revision_id` (`rrv3_`; the derived coverage claim is excluded
   from the preimage so policy evolution never re-mints identities) from
   series key + stated source revisions +
   factual content under the versioned `fb1` boundary
   (`src/app/reporting_identity/capture_binding.py`), persists it in side
   columns (migration 020), exposes it on job diagnostics, and replay-clones
   inherit it verbatim; failed captures and pre-identity history stay NULL.
   Coverage is computed per source from QUALIFYING revision evidence only
   (content hash, snapshot id, restatement version, batch fingerprint, run
   id) — catalogue identity, quality labels, and bare timestamps never
   establish it, and complete coverage asserts neither source-cut
   coherence nor reconciliation.
   The revision hands off downstream: the render package carries it in
   `render_context` and the custody block passes it verbatim into Archive's
   document record (archive migration 011), with resumed renders resolving
   the persisted render job before any resubmission so package-shape
   evolution can never strand an in-flight job as a conflict.
   The synthetic evidence-pack source fingerprint is retired across all
   three federation layers, and the accepted document contract is one
   durable job fact: EVERY axis (family/type, input-snapshot schema,
   report-data contract, envelope version, template pair, locale, brand,
   disclosure baseline) resolves once at acceptance
   (`accepted_document_contract` in `report_ordering_catalogue/`), persists
   on the job (migration 021), and is consumed by capture and the render
   envelope — a replay inherits it verbatim, regeneration resolves current,
   legacy jobs resolve current definitions with no accepted-contract claim.
   The scheduler's duplicated template selection is retired: scheduling is
   portfolio-review-only (the family is bound in the definition validator
   and dispatch), schedules state only output formats, reporting
   currency, and composition options, and the accepted document contract is the one resolved record
   per job.
   Recovery is deployment-safe end to end (report#303 closed): a resumed
   render resolves the persisted render BEFORE any package recomposition
   (a completed v1 job adopts its owner outcome across a composer
   upgrade); waiting on an in-progress render is NOT failure - the work
   queue defers without burning the bounded failure budget and the
   eventual outcome is adopted under the same render id; stale-work
   escalation is Render's diagnostics contract through an explicit
   (recovery_action -> category, retry_eligible) mapping that fails
   closed on unmapped values.
   Two merged-review closures harden the same seam: the idempotency
   request hash is the CLIENT's request — server-derived enrichment
   (`SERVER_DERIVED_REQUEST_OPTION_KEYS`) never enters it, and records
   stored under older hash policies are accepted by recomputing the client
   identity from the record's own persisted request
   (`client_identity_hash_from_record`), so no deployment must reproduce
   historical enrichment; and snapshot lifecycle claims state capability,
   never commands — `reproduction_availability: snapshot_recomposition`
   (what the snapshot holds) is separate from diagnostics'
   `rerender_available` (derived from the same `rerender_eligible`
   predicate that gates the command, so claim and command cannot
   disagree). Legacy policy 1.0.0 rows stamped `rerender_from_snapshot`
   translate at the stores' shared row-to-record read boundary
   (`read_lifecycle` in `_record_from_row`) while replay clones inherit
   the STORED bytes verbatim via `get_stored_lifecycle` — history is
   never rewritten and no version/value pair a policy never stamped can
   be persisted.
   The 17-point integrated proof is COMPLETE (#316, PR #317,
   `tests/integration/test_integrated_lifecycle_proof.py`). Its evidence
   boundary, stated exactly: real PostgreSQL for the ledger, snapshot
   store, batch ledger and every persistence, concurrency and restart
   assertion; owner-shaped DOUBLES at the upstream provider and the
   Render/Archive client seams (built from those owners' shipped response
   bodies). It does not exercise live sibling deployments, and no
   assertion in it should be read as proving one.
   Portfolio-memory event identity is stable from event time (#283): the
   preimage states only facts fixed when the status event was written -
   job, transition, portfolio, timestamp, preimage version `eip2`. It
   previously hashed the job's CURRENT snapshot, artifact and
   archive-document facts, so a finished historical event changed
   identity as the job progressed through capture and archiving, within a
   single deployment and with no code change; a consumer deduplicating on
   it would re-ingest the same event once per lifecycle step. Facts that
   arrive later (snapshot, artifact, archive document, and the report
   revision) reach consumers as refs OUTSIDE the preimage, each attached
   only to events at or after the step that produced it, so body and
   identity tell the same story. Compatibility is stated from retained
   evidence only: v1 hashes were computed at read time and never
   persisted, so they are NOT reconstructible and nothing claims
   otherwise - consumers re-key once by the stable `event_id`.
   Remaining #283 dependency: the reconciliation policy, now owned by
   #321 (Platform #780's promotion condition). Unknown reconciliation
   stays unknown until an explicit policy proves otherwise.
   Design decisions (hash boundary, no circular identity, historical
   mapping) are recorded in the 2026-09-05 audit, on #283, and in
   `src/app/reporting_identity/identity.py`'s module docstring.
2. **#177 — tenant-safe materialization.** Broad `all_active_portfolios`
   scheduling stays refused (fail-closed). Remaining: verify a
   *source-attributed* tenant once `lotus-core` projects tenant identity on
   discovery/detail. Caller admission (shipped) answers who asked — not
   ownership. The deleted stamping path must not be recreated.
3. **Product queue (opens after #283 closure):** #288 source-stated benchmark
   series for the cumulative chart (highest render-blocked value; contract to
   include the stated valuation-vs-source date pair), then #289 drawdown
   series + recovery episodes. #254's producer halves are SHIPPED (return
   attribution + risk attribution; template v3 renders it); its remaining
   scope is only the evidence-gated default-on decision. #271 fallback
   deletion stays evidence-gated on the Render deployment window.

## Known Blockers

| Blocked | On | Why |
|---|---|---|
| #177 source-attributed tenant | `lotus-core` discovery projecting tenant | Core owns `Portfolio.tenant_id` but the discovery route/DTO project no tenant field |
| #254 default-on flip | Named real order/pending evidence | An evidenced decision, not a schedule |
| #271 derivation cleanup | Render deployment window provably closed | Source alone cannot prove every deployed responder upgraded |
| v4 template switch | User publication decision | v3/v4 are development; availability ≠ publication authority |

---

## Working agreements

- **Per-PR contract.** State: report outcome · source authority · report semantic · failure policy
  · render contract · lineage · simplification. Prefer one reporting semantic, one lifecycle
  invariant, or one cross-service contract per PR.
- **New analytics** must answer a real reader question, deepen an existing section before claiming
  a page, and be agreed with the Render session as a typed semantic block *before* either side
  builds.
- **Never** weaken a test to make it pass, preserve a poor path because it exists, or add
  speculative abstraction.
- **CI/governance changes** need a demonstrated correctness, security, lifecycle-integrity,
  reproducibility or release-integrity risk that shared controls cannot own.
- **Review findings become GitHub issues, not local notes.** Before filing, search existing issues
  by file name, lens label and failure pattern, and reuse a **duplicate** when root cause and
  acceptance criteria already match; one high-value issue per validated finding or coherent
  cluster, carrying evidence, expected direction, acceptance criteria and duplicate-search proof.
  Methodology lives in [Codebase Review Playbook](docs/architecture/CODEBASE-REVIEW-PLAYBOOK.md);
  historical closure evidence in
  [Codebase Review Ledger](docs/architecture/CODEBASE-REVIEW-LEDGER.md); the campaign's
  issue-discovery ledger is
  [GitHub issue #109](https://github.com/sgajbi/lotus-report/issues/109). Active backlog state
  lives in GitHub issues, never only in the local ledger.

## Repo-Native Commands

| Purpose | Command |
|---|---|
| install | `make install` |
| fast local gate | `make check` |
| PR-grade gate (caller-owned database) | `make ci` |
| PR-grade gate (helper-managed database) | `make ci-local` |
| coverage gate | `make test-coverage` |
| prior-schema upgrade proof | `make migration-upgrade-smoke` |
| refresh the dependency closure (lane image, Linux resolve) | `make constraints-refresh`, then `make security-audit` in the same slice |
| docker build | `make docker-build` |

The committed `constraints.txt` is the exact dependency closure CI builds and type-checks
against (#345): `make install` applies it, the Docker image's runtime subset installs under it,
and `dependency-constraints-gate` (inside `code-health-gates`) fails on any drift between the
running environment and the recorded closure. It is Linux-resolved and Linux-enforced — pip
environment markers make one exact closure platform-specific, so on other platforms the gate
prints an explicit not-evaluable instead of comparing against a closure wrong for the host by
construction (the lotus-gateway Ubuntu-only-gate posture). `pyproject.toml` floors remain the
compatibility statement; the constraints file is the reproducibility statement and never a
vulnerability-exception mechanism.

Production-like direct access must set `ENTERPRISE_ENFORCE_AUTHZ=true`,
`ENTERPRISE_ENFORCE_READ_AUTHZ=true` and `ENTERPRISE_PRIMARY_KEY_ID`.

## Persistence Topology

Two durable stores, on two engines, with different guarantees.

The **PostgreSQL job and batch ledgers** hold the reporting lifecycle and are persisted by the
`lotus-report-postgres-data` volume, governed by `docs/standards/migration-contract.md`:
forward-only schema management, deterministic smoke validation, and `make migration-upgrade-smoke`
proving a populated volume upgrades in place.

The **Idea evidence intake ledger** is SQLite at `IDEA_EVIDENCE_INTAKE_LEDGER_PATH`, defaulting to
`data/idea-evidence-intake.sqlite3`, persisted by the `lotus-report-intake-data` volume mounted at
`/app/data` on the API service **only** — the API router is its sole constructor, and sharing one
SQLite file between containers would trade a durability gap for multi-writer corruption. Before
that volume existed the file sat in the container's ephemeral layer, so container replacement
destroyed intake evidence and silently reset idempotency, since `idempotency_key` is the replay
identity itself.

**Upgrading a deployment that predates the volume requires a one-time rollout** — stop the API,
carry the existing ledger into the volume, verify the row count, delete the export. It is in
[Operations Runbook](wiki/Operations-Runbook.md) under *One-time rollout: Idea intake ledger
volume*. Skipping it starts the service on an empty ledger with no error.

The intake ledger is **migrating to PostgreSQL** ([#326](https://github.com/sgajbi/lotus-report/issues/326)).
Migration 024 creates `idea_evidence_intake`, which is now a mandatory table in
`make migration-smoke` and has its column types and indexes asserted by
`make migration-upgrade-smoke`. `PostgresIdeaEvidenceIntakeLedger` implements the same surface and
is selected by `REPORT_IDEA_EVIDENCE_INTAKE_LEDGER_BACKEND`.

**The default is still `sqlite`, so the durability gap is not closed.** The transfer exists and is
proven — `scripts/transfer_idea_evidence_intake.py`, sequenced in
[Operations Runbook](wiki/Operations-Runbook.md) under *Cutover: Idea intake ledger to
PostgreSQL* — but no environment has run it. Until one does, pointing a deployment at the new
table would start it from an empty intake ledger — report rows surviving while the evidence that validated them does not, which is the
unverifiable-replay state the materialization route refuses. Read the migration contract as
covering the PostgreSQL **table**, not the store production actually uses, until the transfer and
its rollout acceptance land.

## Validation And CI Expectations

The commands above are the entry points; these are the expectations they exist to satisfy.
Every governed lane must be able to fail for a real reason, and be proven to.

Gate reachability is itself enforced: `tests/unit/test_gate_reachability.py` requires every
gate-shaped target to be reachable from `check`/`ci` **and** executed by both `pr-merge-gate.yml`
and `main-releasability.yml` independently — a lane missing from either allows an unvalidated
merge or leaves the merged revision unvalidated. `scripts/audit_main_gate_coverage.py` (scheduled,
fail-closed) additionally proves every commit on `main` carries a verdict-bearing releasability
run.

Per-revision dispatch follows the platform merged-revision dispatch conformance contract
(lotus-platform `platform-contracts/ci-governance/merged-revision-dispatch-conformance.v1.json`)
in its script form (#364): `scripts/dispatch_merged_revisions.py` is the whole dispatch step,
enumerating landed revisions base..tip (never by commit count — the count walk overreached on
dropped-duplicate rebases), refusing empty intervals, intervals exceeding the PR's stated
commits, revisions off the freshly fetched main, merge commits and contiguity breaks, then
tagging `main-releasability-<revision>` (immutable-ref identity, conflict refused) and
dispatching sequentially oldest-first with `expected_sha` pinned. Conformance is declared in
`.github/merged-revision-dispatch.conformance.json`, bound to the exact workflow run command,
with the behavior tests it names as proofs (real temporary git histories plus a scripted fake
`gh` for tag/dispatch ordering and refusal-before-effect).

Branch protection is asserted, not assumed. `quality/branch_protection_policy.v1.json` records
every protection field this repository claims — required contexts, posture flags, bypass
allowances, CODEOWNERS posture, the review authority, and `documented_exceptions` each carrying the
condition that retires it — and `scripts/check_branch_protection_policy.py` compares live
protection against it field by field, comparing for EQUALITY rather than as a floor, so protection
that weakens OR strengthens away from the table fails and must be re-declared, with absent settings
compared as ABSENT rather than coerced to false. The checker is lifted BYTE-IDENTICALLY from the
canonical implementation (`lotus-gateway` at `main`) and must stay that way: that identity is how a
canonical fix reaches every adopter instead of forking an estate-wide control, so
repository-specific needs belong in the policy table, never in the script. The offline
document-shape checks run blocking in the unit gate so the table cannot rot; the live comparison
runs daily in its own job in `Main Gate Coverage Audit` — a separate job, because sharing the
coverage audit's job would let that job's timeout cancel the protection evidence exactly when it is
most useful. **Operator requirement:** the live comparison needs a repository Actions secret
(`LOTUS_AUTOMERGE_TOKEN`) carrying `administration: read`, which `github.token` cannot carry; no
Lotus repository held one when this landed, so the step FAILS CLOSED on the missing token rather
than passing silently, and the gate's own context is deliberately not yet self-anchored in the
required list — requiring it would block every merge on an operator action rather than assert a
control. Two comparison gaps are stated in the table rather than implied: source `app_id`
bindings (lotus-gateway#740) and only the zero-approval exception being bound to the weakness it
documents (lotus-gateway#743) — so an exception the offline validation does not name can be deleted
while the weakness it excused persists, and only the zero-approval case is genuinely bound. Both are
canonical gaps, neither closable from the table side. A third, four protection controls the checker's
hard-coded allowlist ignored, is **closed**: lotus-gateway#742 corrected the canonical checker to
compare `lock_branch`, `required_signatures`, `block_creations` and `allow_fork_syncing`, and this
repository adopted that copy verbatim and declares all four.

## Standards And RFCs That Govern This Repository

Repository-local standards live in [`docs/standards/`](docs/standards/) and are binding on changes
in their area rather than advisory:

| Standard | Governs |
|---|---|
| [`data-model-ownership.md`](docs/standards/data-model-ownership.md) | the service boundary for domain data, the persisted entities Report does own, and the glossary rule against service-local synonyms |
| [`durability-consistency.md`](docs/standards/durability-consistency.md) | what must survive restart, and what a retry may observe |
| [`migration-contract.md`](docs/standards/migration-contract.md) | schema change rules for the **PostgreSQL** job and batch ledgers, including what must never be deleted for audit and reconciliation. It does NOT cover the SQLite intake ledger - see the gap noted in `data-model-ownership.md` |
| [`rounding-precision.md`](docs/standards/rounding-precision.md) | monetary and numeric handling, enforced by the float guard |
| [`batch-orchestration-source-map.md`](docs/standards/batch-orchestration-source-map.md) | batch posture fields and their sources |
| [`enterprise-readiness.md`](docs/standards/enterprise-readiness.md) | authz, key handling and production-like posture |
| [`scalability-availability.md`](docs/standards/scalability-availability.md) | load, concurrency and degradation expectations |
| [`dependency-vulnerability-exceptions.md`](docs/standards/dependency-vulnerability-exceptions.md) | which advisories are accepted, with expiry |

RFCs authored here live in [`rfcs/`](rfcs/).
[`docs/standards/rfc-traceability.md`](docs/standards/rfc-traceability.md) maps every active RFC to
the code and tests that implement it, and is the file to update when an RFC gains or loses
implementation evidence - a traceability entry with no evidence pointer is a claim, not a record.

Estate-wide contracts bind this document itself: `lotus-platform/context/AGENTS-OPERATING-CONTRACT.md`
governs how a session works here, and `lotus-platform/context/Repository-Engineering-Context-Contract.md`
sets the minimum section shape below. Sections beyond that minimum, such as
[What Report Must Never Own](#what-report-must-never-own), are deliberate additions.

## Context Maintenance Rule

report ownership or boundaries move · the lifecycle or its recovery paths change · a major contract
is added or retired · an invariant is added, removed or weakened · priorities or blockers change.

Everything else — what shipped, when, and under which RFC slice — belongs in the review ledger,
the RFCs, the wiki, or GitHub issues.

## Cross-Links

1. `../lotus-platform/context/LOTUS-QUICKSTART-CONTEXT.md`
2. `../lotus-platform/context/LOTUS-ENGINEERING-CONTEXT.md`
3. `../lotus-platform/context/CONTEXT-REFERENCE-MAP.md`
4. `../lotus-platform/context/Repository-Engineering-Context-Contract.md`
5. [Lotus Developer Onboarding](../lotus-platform/docs/onboarding/LOTUS-DEVELOPER-ONBOARDING.md)
6. [Lotus Agent Ramp-Up](../lotus-platform/docs/onboarding/LOTUS-AGENT-RAMP-UP.md)
7. [Codebase Review Playbook](docs/architecture/CODEBASE-REVIEW-PLAYBOOK.md)
8. [Codebase Review Ledger](docs/architecture/CODEBASE-REVIEW-LEDGER.md)
