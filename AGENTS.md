# Lotus Agent Operating Contract

This is the governed operating contract for Lotus agent work.

Repo-root `AGENTS.md` files across Lotus repositories and the deployed local `AGENTS.md` should
remain synchronized copies of this file.

Use `automation/Sync-AgentOperatingContract.ps1` to synchronize or verify that deployed copy.

## Mandatory Reading Order

Before doing substantial work, load context in this order:

1. `AGENTS.md`
2. `lotus-platform/context/LOTUS-QUICKSTART-CONTEXT.md`
3. `lotus-platform/context/LOTUS-ENGINEERING-CONTEXT.md`
4. the target repository's `REPOSITORY-ENGINEERING-CONTEXT.md`
5. `lotus-platform/context/CONTEXT-REFERENCE-MAP.md`
6. `lotus-platform/context/PROCEDURAL-MEMORY-INDEX.md` when the task is primarily about how work should be executed

Use the smallest correct working set. Do not load broad context blindly if the task is narrow.

## Mandatory Operating Rules

Always:

1. reduce complexity where possible,
2. improve readability, maintainability, and modularity as part of the slice,
3. make code and test improvements that materially improve reliability and maintainability,
4. update documentation when platform or repository truth changes,
5. leave the codebase cleaner than you found it,
6. write meaningful, high-value tests and avoid superficial coverage,
7. keep commits small, meaningful, and truthful,
8. remove dead code, duplicate logic, and stale non-standard handling when encountered,
9. ensure every UI feature is genuinely backed by supported backend functionality,
10. ensure RFC/docs/wiki/context/contract closure truth is present on `main`, not stranded on an
    unmerged side branch.

For RFC, documentation, wiki, context, contract, supported-features, API-governance, migration, or
CI-workflow changes, run stranded-truth reconciliation before starting implementation, before final
closure, and before moving to the next RFC:

1. `git fetch origin --prune`,
2. `git branch -r --no-merged origin/main`,
3. inspect unmerged branches that touch durable governance paths,
4. classify each as `must-merge`, `cherry-pick`, `superseded`, `delete`, or `active`,
5. merge, cherry-pick, explicitly supersede, or delete unique durable truth before claiming closure.

## Delivery Posture

Operate as a banking-grade engineer, not a generic coding assistant.

That means:

1. prefer truthful implementation over cosmetic output,
2. prefer reusable patterns over local hacks,
3. treat naming, contracts, tests, docs, and validation as part of the implementation,
4. use domain-correct private banking, portfolio, advisory, performance, and risk language.

## Skills, Automation, And Async Execution

When the task matches an available Lotus skill, use it.

Before choosing between overlapping Lotus skills, consult
`lotus-platform/context/LOTUS-SKILL-ROUTING-MAP.md`.

Prefer:

1. standards, validators, and runbooks before inventing a new pattern,
2. repo-native commands before ad hoc command sequences,
3. targeted local checks for quick proof,
4. GitHub-backed heavy execution for expensive full validation,
5. async monitoring and fix-forward work rather than blocking on long reruns.

For long-running, delegated, async, or context-compacted work, use
`lotus-platform/context/playbooks/AGENT-CONTEXT-AND-TASK-LEDGER.md`. Preserve operational
identifiers exactly, including repository, branch, PR number, commit SHA, check name, RFC id, file
path, endpoint, contract name, portfolio id, `engineering_task_id`, and task status. Treat
`output/background-runs.json` as local automation evidence and GitHub Actions as GitHub check truth.

For multi-agent delegation, use the governed profiles and envelopes in
`lotus-platform/platform-contracts/agent-engineering/delegation-policy-contract.v1.json`.
Delegate only bounded non-blocking work with explicit read scope, explicit write scope or `none`,
required evidence, and a required return envelope. Keep the main agent accountable for diff review,
integration, tests, PR posture, wiki publication, and final communication. Do not delegate broad
repo cleanup, immediate critical-path blockers, overlapping write scopes, PR merge, or wiki
publication unless the main agent explicitly owns and reviews the final action.

## Wiki Publication Rule

When documentation, RFC, context, runbook, or operator-facing truth changes:

1. update the repo-local `wiki/` source in the same PR when wiki truth changed,
2. record an explicit no-wiki-change decision when no wiki update is needed,
3. run `lotus-platform/automation/Sync-RepoWikis.ps1 -CheckOnly -Repository <repo-name>` before merge,
4. after merge to `main`, publish with
   `lotus-platform/automation/Sync-RepoWikis.ps1 -Publish -Repository <repo-name>`,
5. use `-AllRepositories` only for platform-wide audits or coordinated publication sweeps.

Repo-local `wiki/` is the authored source of truth. The separate GitHub `*.wiki.git` repository is
only the publication target and must not receive hand-edited truth that is absent from repo source.

When a task is explicitly about canonical populated Workbench surfaces, demo screenshots, or
`PB_SG_GLOBAL_BAL_001`, choose `lotus-front-office-runtime` first and use broader QA or delivery
skills only as supporting guidance.

## Front-Office Runtime Routing Rule

When the task is about:

1. local front-office runtime bring-up,
2. populated Workbench screens,
3. panel validation,
4. demo screenshots,
5. canonical UI proof,

use the governed `lotus-workbench` runtime and validation flow first:

1. `lotus-workbench/docs/operations/canonical-front-office-local-runtime.md`
2. `npm run live:stack:up`
3. `npm run live:validate`
4. `npm run live:stack:down`
5. `lotus-platform/automation/Invoke-Canonical-FrontOffice-QA.ps1 -ScreenshotDirectory <path>` when the task needs platform-owned validation evidence and a caller-directed demo screenshot pack
6. `lotus-platform/context/contracts/canonical-front-office-demo-data-contract.json`
7. `lotus-platform/context/contracts/canonical-front-office-demo-data-invariants.json`

Use `PB_SG_GLOBAL_BAL_001` as the governed seeded front-office portfolio unless the task explicitly requires another dataset.

Treat the RFC-0076 contract files as the source of truth for canonical portfolio identity, benchmark
identity, governed as-of date, and minimum supportability thresholds. Runtime evidence should carry
contract provenance instead of relying on implicit repo convention.

Do not treat `lotus-platform/platform-stack` as the canonical front-office product bring-up path. It owns shared ingress and infrastructure support, not the full governed product-surface flow.

Do not capture or share demo-ready screenshots before canonical API, calculation, and panel validation pass. If a pre-validation capture is necessary for diagnosis, label it with a `diagnostic-` prefix and keep it separate from demo evidence.

## Context Maintenance Rule

Keep the context system up to date as Lotus changes.

Update the relevant context artifacts when:

1. platform architecture changes,
2. repository responsibilities change,
3. canonical commands or validation flows change,
4. CI or governance expectations change,
5. a repeatable pattern should become durable guidance,
6. domain vocabulary or operating assumptions materially change.

If the change is platform-wide:

1. update the central context system in `lotus-platform/context/`.
2. update `LOTUS-SKILL-ROUTING-MAP.md` if task routing expectations changed.

If the change is repository-local:

1. update that repository's `REPOSITORY-ENGINEERING-CONTEXT.md`.

If both changed:

1. update both in the same slice.

## Cross-Links

Central context system:

1. `<lotus-platform>/context/LOTUS-QUICKSTART-CONTEXT.md`
2. `<lotus-platform>/context/LOTUS-ENGINEERING-CONTEXT.md`
3. `<lotus-platform>/context/CONTEXT-REFERENCE-MAP.md`
4. `<lotus-platform>/context/PROCEDURAL-MEMORY-INDEX.md`
5. `<lotus-platform>/context/LOTUS-SKILL-ROUTING-MAP.md`
6. `<lotus-platform>/context/lotus-context-manifest.json`
7. `<lotus-platform>/context/platform-engineering-ledger.md`
8. `<lotus-platform>/context/recent-architectural-decisions-digest.md`
9. `<lotus-platform>/docs/onboarding/LOTUS-DEVELOPER-ONBOARDING.md`
10. `<lotus-platform>/docs/onboarding/LOTUS-AGENT-RAMP-UP.md`

Repository-local context:

1. `REPOSITORY-ENGINEERING-CONTEXT.md` in the repository you are changing

When the central contract changes, keep both this source file and the deployed `AGENTS.md` synchronized.
