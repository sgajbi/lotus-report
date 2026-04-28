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

## Sign-Off Standard

Do not sign off an RFC slice from happy-path behavior alone. The review must check source-backed
contracts, idempotency or replay safety where relevant, no-sensitive-content posture, OpenAPI/docs
alignment, and repo-native or GitHub CI evidence.
