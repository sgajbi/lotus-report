"""The stamped lifecycle claim stays consistent with the governed
contract file - one policy statement, two representations, zero drift."""

from __future__ import annotations

import json
from pathlib import Path

from app.reporting_identity.snapshot_lifecycle import (
    SNAPSHOT_LIFECYCLE_POLICY_REF,
    snapshot_lifecycle_claim,
)

_CONTRACT = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "report-input-snapshot"
    / "lotus-report-input-snapshot-lifecycle.v1.json"
)


def test_the_stamped_policy_exists_in_the_governed_contract() -> None:
    contract = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    policies = {policy["policy_ref"]: policy for policy in contract["policies"]}

    claim = snapshot_lifecycle_claim(capture_failed=False)
    assert claim["policy_ref"] in policies
    policy = policies[claim["policy_ref"]]
    assert claim["policy_version"] == policy["policy_version"]
    assert claim["reproduction_availability"] == policy["reproduction_availability"]


def test_a_failed_capture_states_no_reproduction() -> None:
    claim = snapshot_lifecycle_claim(capture_failed=True)
    assert claim["reproduction_availability"] == "none"
    assert claim["policy_ref"] == SNAPSHOT_LIFECYCLE_POLICY_REF


def test_the_contract_declares_no_second_retention_engine() -> None:
    """The audit's explicit boundary: no retention automation here, and the
    document lifecycle authority is lotus-archive - both stated verbatim in
    the governed contract."""

    contract = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    assert contract["document_lifecycle_authority_repository"] == "lotus-archive"
    assert "no second retention or legal-hold engine" in contract["statement"]
