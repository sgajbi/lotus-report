# Codebase Review Playbook

This playbook defines the review standard for governed `lotus-report` implementation audits.

## Review Units

Use implementation slices as the default review unit for RFC-driven work. For RFC-0105, the review
units are platform scaffold hardening, observability vocabulary, trace propagation, metrics and
dashboard contracts, diagnostics, rerender, regenerate, replay, attention scanning, live proof, and
closure documentation.

## Status Model

| Status | Meaning |
| --- | --- |
| Signed off | Scope has source-backed implementation, meaningful tests, documented evidence, and no material unresolved finding. |
| Hardened | Scope was improved during the review and has targeted evidence, with only explicitly bounded follow-up remaining. |
| Refactor needed | Scope works but carries material maintainability or correctness debt. |
| Blocked | Scope cannot be signed off because required evidence or dependent implementation is missing. |

## Evidence Requirements

Each signed-off or hardened scope must record:

1. code files reviewed or changed,
2. behavior and risk covered by tests,
3. live/runtime evidence when the scope crosses service boundaries,
4. documentation or wiki posture when operator truth changed,
5. remaining follow-up if the scope intentionally defers broader product behavior.

## GitHub Issue-Discovery Workflow

Use GitHub issues as the active backlog for validated findings. The local review ledger records
methodology, historical evidence, and closure manifests; it must not become an unlinked backlog.

Before filing or fixing a review finding:

1. search existing issues with the affected file names, lens names, failure pattern, and domain
   vocabulary, for example:
   `gh issue list --repo sgajbi/lotus-report --state all --search "<pattern> <file> <lens>"`;
2. reuse an existing issue when it already captures the same root cause or acceptance criteria;
3. create one high-value GitHub issue per validated finding or tightly related finding cluster;
4. add `issue-discovery`, the relevant `lens/*` labels, and at least one `impact/*` label;
5. link the issue from the review ledger only after the finding is accepted for implementation or
   fixed locally;
6. update the discovery ledger issue, currently
   [#109](https://github.com/sgajbi/lotus-report/issues/109), with the issue number, lens,
   duplicate-search proof, and current status.

Each issue-discovery finding must include:

1. evidence with source file references and line numbers where practical,
2. expected direction,
3. acceptance criteria,
4. duplicate-search proof with the exact GitHub queries used,
5. validation proof or a clear statement that the finding was inspection-only,
6. same-pattern scan notes when implementation begins,
7. closure evidence after the branch contains code, test, docs, wiki, or context changes required
   by the acceptance criteria.

Use this discovery-ledger comment format when adding or updating #109:

```text
Finding: #<issue> - <title>
Lens: <lens labels>
Duplicate search: <queries or "reused existing issue #...">
Status: <new|in-progress|fixed-local|pr-open|merged-main|closed>
Validation: <inspection, focused tests, make target, or pending>
Next: <owner/action or "none">
```

Do not duplicate active issue state in this playbook or ledger unless the corresponding GitHub
issue is linked. If a historical ledger entry is superseded by GitHub-backed tracking, keep the
historical entry for audit context and point future work to the issue.

## Sign-Off Standard

Do not sign off an RFC slice from happy-path behavior alone. The review must check source-backed
contracts, idempotency or replay safety where relevant, no-sensitive-content posture, OpenAPI/docs
alignment, and repo-native or GitHub CI evidence.
