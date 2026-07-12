import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.idea_evidence_intake.retention_policy import (
    IdeaEvidenceRetentionPolicy,
    InMemoryIdeaEvidenceRetentionPolicyRegistry,
    RetentionPolicyError,
)


def test_registry_resolves_authorized_effective_policy() -> None:
    policy = _policy()
    registry = InMemoryIdeaEvidenceRetentionPolicyRegistry({policy.policy_ref: policy})

    resolved = registry.resolve(
        policy_ref=policy.policy_ref,
        tenant_id="tenant-sg",
        producer="lotus-idea",
        at_utc=datetime(2026, 6, 24, tzinfo=UTC),
    )

    assert resolved is policy


def test_default_registry_matches_versioned_policy_contract() -> None:
    contract_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "idea-evidence-intake"
        / "lotus-report-idea-evidence-retention-policy.v1.json"
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    registry = InMemoryIdeaEvidenceRetentionPolicyRegistry()

    for expected in contract["policies"]:
        resolved = registry.resolve(
            policy_ref=expected["policy_ref"],
            tenant_id=expected["authorized_tenants"][0],
            producer=expected["authorized_producers"][0],
            at_utc=datetime(2026, 6, 24, tzinfo=UTC),
        )
        actual = {
            "policy_ref": resolved.policy_ref,
            "policy_version": resolved.policy_version,
            "purpose": resolved.purpose,
            "retention_start_event": resolved.retention_start_event,
            "retention_duration_days": resolved.retention_duration_days,
            "approval_authority": resolved.approval_authority,
            "residency_region": resolved.residency_region,
            "authorized_tenants": sorted(resolved.authorized_tenants),
            "authorized_producers": sorted(resolved.authorized_producers),
            "legal_hold_active": resolved.legal_hold_active,
            "erasure_action": resolved.erasure_action,
            "archive_handoff_policy": resolved.archive_handoff_policy,
            "effective_from_utc": resolved.effective_from_utc.isoformat().replace("+00:00", "Z"),
        }
        assert actual == expected


@pytest.mark.parametrize(
    ("policy_ref", "tenant_id", "producer", "at_utc", "expected_code"),
    [
        (
            "unknown",
            "tenant-sg",
            "lotus-idea",
            datetime(2026, 6, 24, tzinfo=UTC),
            "unknown_retention_policy",
        ),
        (
            "generated-report-standard",
            "tenant-uk",
            "lotus-idea",
            datetime(2026, 6, 24, tzinfo=UTC),
            "retention_policy_tenant_mismatch",
        ),
        (
            "generated-report-standard",
            "tenant-sg",
            "unapproved-producer",
            datetime(2026, 6, 24, tzinfo=UTC),
            "unauthorized_retention_policy_producer",
        ),
        (
            "generated-report-standard",
            "tenant-sg",
            "lotus-idea",
            datetime(2027, 1, 1, tzinfo=UTC),
            "inactive_retention_policy",
        ),
    ],
)
def test_registry_rejects_invalid_policy_authority(
    policy_ref: str,
    tenant_id: str,
    producer: str,
    at_utc: datetime,
    expected_code: str,
) -> None:
    policy = _policy(effective_until_utc=datetime(2027, 1, 1, tzinfo=UTC))
    registry = InMemoryIdeaEvidenceRetentionPolicyRegistry({policy.policy_ref: policy})

    with pytest.raises(RetentionPolicyError) as caught:
        registry.resolve(
            policy_ref=policy_ref,
            tenant_id=tenant_id,
            producer=producer,
            at_utc=at_utc,
        )

    assert caught.value.code == expected_code


def _policy(*, effective_until_utc: datetime | None = None) -> IdeaEvidenceRetentionPolicy:
    return IdeaEvidenceRetentionPolicy(
        policy_ref="generated-report-standard",
        policy_version="1.0.0",
        purpose="GOVERNED_CLIENT_REPORT_EVIDENCE",
        retention_start_event="REPORT_ARCHIVED",
        retention_duration_days=2557,
        approval_authority="lotus-report-information-governance",
        residency_region="APAC",
        authorized_tenants=frozenset({"tenant-sg"}),
        authorized_producers=frozenset({"lotus-idea"}),
        legal_hold_active=False,
        erasure_action="REDACT_EVIDENCE_REFERENCES_AFTER_APPROVAL",
        archive_handoff_policy="lotus-archive:idea-evidence-retention:v1",
        effective_from_utc=datetime(2026, 1, 1, tzinfo=UTC),
        effective_until_utc=effective_until_utc,
    )
