# Repository Engineering Context

Operating map for `lotus-report`. It answers what this service owns, what it must never own, how
a report is produced, and what is true right now.

**This is not a changelog.** Delivery chronology and RFC-slice narration live in
`docs/architecture/CODEBASE-REVIEW-LEDGER.md`, the RFCs, the wiki, and GitHub issues. If a fact
here is only interesting because of when it shipped, it belongs there instead.

---

## 1. What Report owns

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

## 2. What Report must never own

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

## 3. Architecture

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

## 4. The report lifecycle

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

## 5. Major contracts

- **Upstream (read):** `lotus-core` portfolio summary and asset allocation; `lotus-performance`
  workspace summary and contribution; `lotus-risk` analytics; `lotus-ai` accepted-output
  projection and latest-accepted lookup.
- **Downstream (write):** `lotus-render` render package; `lotus-archive` document handoff.
- **Consumer-facing:** the report ordering catalogue, report job APIs, job lineage, and the
  advisor-commentary availability seam consumed by `lotus-gateway`.
- Repo-native domain-data-product declarations live in `contracts/`; `lotus-performance` and
  `lotus-risk` remain **watchlisted** consumers, so analytics-enriched evidence must not publish
  complete, unblocked trust telemetry.

## 6. Key invariants

1. **Explicit unavailable beats plausible but wrong.** Every absence has a bounded reason code;
   nothing is defaulted into looking complete.
2. **Posture is stated, never inferred.** `ready` / `empty` / `unavailable` are different claims:
   *empty* is a fact about the portfolio and is drawn, *unavailable* is a fact about the data and
   is said. A consumer must never infer meaning from an empty list or a present key.
3. **A transport failure is not a downstream failure.** Timeout ≠ absence; render completion ≠
   archived; a retry must never mint a duplicate client document.
4. **Uncertain outcomes resolve before retrying**, and one lineage yields at most one archived
   document.
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

## 7. Active priorities

1. **#177 — tenant-safe materialization.** Broad `all_active_portfolios` scheduling is refused
   (fail-closed) rather than stamped. Remaining: verify a *source-attributed* tenant once
   `lotus-core` projects it.
2. **#166 — advisor commentary.** JSON path complete: admissibility validated (accepted,
   non-superseded, tenant, portfolio, period, as-of, currency, benchmark, content hash, reviewer,
   review time, source refs, and a complete VALIDATED output verdict) and the accepted projection
   persisted verbatim into the snapshot. Each reviewed claim states its own `grounding` posture.
   Remaining: the PDF leg and Workbench rendering.
3. **#209 — analytics coverage.** Group A (already captured, snapshotted, and previously discarded
   at the package boundary): contribution ranking **(shipped)**, threshold/breach, expected
   shortfall, income/P&L, concentration. Drawdown-as-picture is *not* Group A — episodes and the
   underwater curve need a new capture, so it is scoped separately from the drawdown scalar. Group B (absent from Report entirely) stays unscheduled until a
   reporting question demands it. The canonical cross-repo matrix is `lotus-render#160`.

## 8. Known blockers

| Blocked | On | Why |
|---|---|---|
| #177 source-attributed tenant | `lotus-core#798` S2+ | Core owns `Portfolio.tenant_id` (core#1076) but its discovery route projects no tenant |
| #166 PDF leg | `lotus-render#218` | The template exists (render#223) but does not draw the per-claim `grounding` posture, so an ungrounded AI claim would be indistinguishable from a checkable one in an archived document. Report's PDF gate refuses the section until it does |
| #166 Workbench rendering | `lotus-workbench#795` | Their lane; contract and reason vocabulary delivered |

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

## Commands

| Purpose | Command |
|---|---|
| install | `make install` |
| fast local gate | `make check` |
| PR-grade gate (caller-owned database) | `make ci` |
| PR-grade gate (helper-managed database) | `make ci-local` |
| coverage gate | `make test-coverage` |
| prior-schema upgrade proof | `make migration-upgrade-smoke` |
| docker build | `make docker-build` |

Gate reachability is itself enforced: `tests/unit/test_gate_reachability.py` requires every
gate-shaped target to be reachable from `check`/`ci` **and** executed by both `pr-merge-gate.yml`
and `main-releasability.yml` independently — a lane missing from either allows an unvalidated
merge or leaves the merged revision unvalidated. `scripts/audit_main_gate_coverage.py` (scheduled,
fail-closed) additionally proves every commit on `main` carries a verdict-bearing releasability
run.

Production-like direct access must set `ENTERPRISE_ENFORCE_AUTHZ=true`,
`ENTERPRISE_ENFORCE_READ_AUTHZ=true` and `ENTERPRISE_PRIMARY_KEY_ID`.

## Keep this document current when

report ownership or boundaries move · the lifecycle or its recovery paths change · a major contract
is added or retired · an invariant is added, removed or weakened · priorities or blockers change.

Everything else — what shipped, when, and under which RFC slice — belongs in the review ledger,
the RFCs, the wiki, or GitHub issues.

## Cross-links

1. `../lotus-platform/context/LOTUS-QUICKSTART-CONTEXT.md`
2. `../lotus-platform/context/LOTUS-ENGINEERING-CONTEXT.md`
3. `../lotus-platform/context/CONTEXT-REFERENCE-MAP.md`
4. `../lotus-platform/context/Repository-Engineering-Context-Contract.md`
5. [Lotus Developer Onboarding](../lotus-platform/docs/onboarding/LOTUS-DEVELOPER-ONBOARDING.md)
6. [Lotus Agent Ramp-Up](../lotus-platform/docs/onboarding/LOTUS-AGENT-RAMP-UP.md)
7. [Codebase Review Playbook](docs/architecture/CODEBASE-REVIEW-PLAYBOOK.md)
8. [Codebase Review Ledger](docs/architecture/CODEBASE-REVIEW-LEDGER.md)
