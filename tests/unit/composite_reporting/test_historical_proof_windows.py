"""Recorded proof windows follow the signed producer contract, including real delay."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.composite_reporting import historical_source
from app.composite_reporting.admission import CompositeEvidenceRefused
from app.composite_reporting.eligibility_admission import source_hash

FIXTURES = Path(__file__).parents[2] / "fixtures" / "composite-historical-policy"
NATIVE = json.loads((FIXTURES / "native-admission.json").read_bytes())


class RecordedClock(datetime):
    @classmethod
    def now(cls, _tz=None):
        raise AssertionError("Recorded proof admission must not request the current clock")


@pytest.mark.parametrize("profile", ["v1", "v2"])
@pytest.mark.parametrize("version", ["v3", "v4"])
def test_unchanged_native_proofs_admit_real_network_delay(profile, version, monkeypatch):
    """Public signed products retain actual request/admission clocks and nested hashes."""
    monkeypatch.setattr(historical_source, "datetime", RecordedClock)
    receipt = NATIVE["profiles"][profile]["receipts"][version]
    approval = receipt["approval"]
    proposal = approval["proposal"]
    policy_approval = proposal["policy_approval"]
    policy = policy_approval["proposal"]["policy"]
    scope = SimpleNamespace(
        **policy["scope"],
        reporting_currency=policy_approval["proposal"]["verification"]["mapping"][
            "reporting_currency"
        ],
    )
    proofs = [
        policy_approval["proposal"]["verification"],
        policy_approval["verification"],
        proposal["operation_verification"],
        approval["operation_verification"],
    ]
    for proof in proofs:
        assert datetime.fromisoformat(proof["request"]["requested_at"]) < datetime.fromisoformat(
            proof["admitted_at"]
        )
    historical_source.validate_product(receipt)
    historical_source.validate_product(approval)
    historical_source.validate_product(proposal)
    historical_source.require_policy(scope, policy["month"], proposal)
    historical_source.require_operation(approval, proposal, "EVALUATION_APPROVAL")


def recorded_window(checked, requested, admitted, expiry):
    """Unsigned rehashed clones isolate structural guards; never producer authority."""
    proposal = json.loads(
        (FIXTURES / "CompositeMonthlyEvaluationProposal.v3.controlled.json").read_bytes()
    )
    proof = deepcopy(proposal["operation_verification"])
    base = datetime(2001, 1, 1, tzinfo=timezone.utc)
    for field, offset in (
        ("checked_at", checked),
        ("admitted_at", admitted),
        ("expires_at", expiry),
    ):
        proof[field] = (base + timedelta(seconds=offset)).isoformat()
    proof["request"]["requested_at"] = (base + timedelta(seconds=requested)).isoformat()
    proof["content_hash"] = source_hash(proof)
    return proof, proposal["policy_approval"]["proposal"]


def admit_recorded_window(proof, policy, *, at=None):
    request = proof["request"]
    historical_source.require_proof(
        proof,
        policy,
        operation=request["operation"],
        actor=request["actor_id"],
        revision=request["revision"],
        at=request["requested_at"] if at is None else at,
        intent=request["intent_digest"],
    )


@pytest.mark.parametrize(
    "window",
    [(0, 0, 0, 1), (0, 0, 0.000001, 1), (0, 1, 299, 300), (0, 0, 299.999999, 300)],
    ids=["equal-request-admission", "microsecond-delay", "full-five-minutes", "last-microsecond"],
)
def test_valid_recorded_windows_remain_valid_after_their_expiry(window, monkeypatch):
    monkeypatch.setattr(historical_source, "datetime", RecordedClock)
    admit_recorded_window(*recorded_window(*window))


@pytest.mark.parametrize(
    "window",
    [
        (1, 0, 2, 3),
        (0, 2, 1, 3),
        (0, 2, 2, 2),
        (0, 1, 3, 2),
        (0, 0, 0, 0),
        (1, 1, 1, 0),
        (0, 0, 1, 300.000001),
    ],
    ids=[
        "checked-after-request",
        "admission-before-request",
        "admission-at-expiry",
        "admission-after-expiry",
        "zero-window",
        "negative-window",
        "over-five-minutes",
    ],
)
def test_invalid_recorded_windows_refuse(window):
    with pytest.raises(
        CompositeEvidenceRefused, match="COMPOSITE_HISTORICAL_SOURCE_BINDING_CONFLICT"
    ):
        admit_recorded_window(*recorded_window(*window))


@pytest.mark.parametrize("field", ["checked_at", "requested_at", "admitted_at", "expires_at"])
def test_timezone_naive_recorded_clocks_refuse(field):
    proof, policy = recorded_window(0, 1, 2, 3)
    target = proof["request"] if field == "requested_at" else proof
    target[field] = target[field].removesuffix("+00:00")
    proof["content_hash"] = source_hash(proof)
    with pytest.raises(
        CompositeEvidenceRefused, match="COMPOSITE_HISTORICAL_SOURCE_BINDING_CONFLICT"
    ):
        admit_recorded_window(proof, policy)


def test_valid_delay_preserves_exact_operation_request_clock_binding():
    proof, policy = recorded_window(0, 1, 2, 3)
    with pytest.raises(
        CompositeEvidenceRefused, match="COMPOSITE_HISTORICAL_SOURCE_BINDING_CONFLICT"
    ):
        admit_recorded_window(proof, policy, at=proof["admitted_at"])
