from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Mapping, Protocol


class RetentionPolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class IdeaEvidenceRetentionPolicy:
    policy_ref: str
    policy_version: str
    purpose: str
    retention_start_event: str
    retention_duration_days: int
    approval_authority: str
    residency_region: str
    authorized_tenants: frozenset[str]
    authorized_producers: frozenset[str]
    legal_hold_active: bool
    erasure_action: str
    archive_handoff_policy: str
    effective_from_utc: datetime
    effective_until_utc: datetime | None = None


class IdeaEvidenceRetentionPolicyResolver(Protocol):
    def resolve(
        self,
        *,
        policy_ref: str,
        tenant_id: str,
        producer: str,
        at_utc: datetime | None = None,
    ) -> IdeaEvidenceRetentionPolicy: ...


class InMemoryIdeaEvidenceRetentionPolicyRegistry:
    def __init__(
        self,
        policies: Mapping[str, IdeaEvidenceRetentionPolicy] | None = None,
    ) -> None:
        configured = policies or _default_policies()
        self._policies = MappingProxyType(dict(configured))

    def resolve(
        self,
        *,
        policy_ref: str,
        tenant_id: str,
        producer: str,
        at_utc: datetime | None = None,
    ) -> IdeaEvidenceRetentionPolicy:
        policy = self._policies.get(policy_ref)
        if policy is None:
            raise RetentionPolicyError(
                "unknown_retention_policy",
                "The retention policy reference is not recognized by lotus-report.",
            )
        effective_at = at_utc or datetime.now(UTC)
        if effective_at < policy.effective_from_utc or (
            policy.effective_until_utc is not None and effective_at >= policy.effective_until_utc
        ):
            raise RetentionPolicyError(
                "inactive_retention_policy",
                "The retention policy is not effective at the requested time.",
            )
        if producer not in policy.authorized_producers:
            raise RetentionPolicyError(
                "unauthorized_retention_policy_producer",
                "The producer is not authorized to use the retention policy.",
            )
        if tenant_id not in policy.authorized_tenants:
            raise RetentionPolicyError(
                "retention_policy_tenant_mismatch",
                "The retention policy is not authorized for the caller tenant.",
            )
        return policy


def _default_policies() -> dict[str, IdeaEvidenceRetentionPolicy]:
    return {
        "generated-report-standard": _policy(
            policy_ref="generated-report-standard",
            legal_hold_active=False,
        ),
        "generated-report-legal-hold": _policy(
            policy_ref="generated-report-legal-hold",
            legal_hold_active=True,
        ),
    }


def _policy(*, policy_ref: str, legal_hold_active: bool) -> IdeaEvidenceRetentionPolicy:
    return IdeaEvidenceRetentionPolicy(
        policy_ref=policy_ref,
        policy_version="1.0.0",
        purpose="GOVERNED_CLIENT_REPORT_EVIDENCE",
        retention_start_event="REPORT_ARCHIVED",
        retention_duration_days=2557,
        approval_authority="lotus-report-information-governance",
        residency_region="APAC",
        authorized_tenants=frozenset({"tenant-sg"}),
        authorized_producers=frozenset({"lotus-idea"}),
        legal_hold_active=legal_hold_active,
        erasure_action="REDACT_EVIDENCE_REFERENCES_AFTER_APPROVAL",
        archive_handoff_policy="lotus-archive:idea-evidence-retention:v1",
        effective_from_utc=datetime(2026, 1, 1, tzinfo=UTC),
    )
