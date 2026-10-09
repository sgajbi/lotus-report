from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.composite_reporting.admission import (
    CompositeEvidenceRefused,
    admit_composite_response,
)
from app.composite_reporting.models import CompositeReportSelection
from tests.unit.composite_reporting.fixtures import calculated_example, selection_for


def admit(payload, *, selection=None, tenant="tenant-a", status=200):
    return admit_composite_response(
        selection=selection or selection_for(payload),
        admitted_tenant_id=tenant,
        status_code=status,
        payload=payload,
    )


def test_oracles_are_retained_without_recalculation_or_authority_invention():
    payload = calculated_example()
    dataset = admit(payload)
    assert dataset["source_response"] == payload
    assert dataset["publication_state"] == "NOT_ATTESTED"
    assert dataset["source_response"]["periods"][0]["return_value"] == "0.010000000000"
    assert dataset["source_response"]["cumulative_return"] == "0.030200000000"
    assert dataset["source_response"]["periods"][0]["dispersion_equal_weight"] is None
    payload["periods"][0]["member_contributions"][0]["portfolio_id"] = "changed"
    assert (
        dataset["source_response"]["periods"][0]["member_contributions"][0]["portfolio_id"]
        == "00000000000000000001"
    )


@pytest.mark.parametrize("status", [202, 204, 400, 404, 409, 422, 500, 503])
def test_source_failure_never_becomes_zero_or_empty(status):
    with pytest.raises(CompositeEvidenceRefused, match="SOURCE_UNAVAILABLE"):
        admit(calculated_example(), status=status)


@pytest.mark.parametrize("tenant", ["tenant-b", "", " tenant-a"])
def test_other_tenant_refuses_before_payload_admission(tenant):
    with pytest.raises(CompositeEvidenceRefused, match="TENANT_MISMATCH"):
        admit(calculated_example(), tenant=tenant)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", True, 0.1, [], {}])
def test_invalid_financial_evidence_is_refused(number):
    payload = calculated_example()
    payload["periods"][0]["member_contributions"][0]["contribution"] = number
    with pytest.raises(CompositeEvidenceRefused, match="SOURCE_INVALID"):
        admit(payload)


@pytest.mark.parametrize("number", ["0", "-100.00000000000001", "999999999999999999999999.12"])
def test_zero_negative_and_large_source_values_keep_exact_text(number):
    payload = calculated_example()
    payload["periods"][0]["member_contributions"][0]["contribution"] = number
    assert admit(payload)["source_response"] == payload


def test_changed_authority_or_financial_response_refuses_original_pin():
    payload = calculated_example()
    selection = selection_for(payload)
    changed = deepcopy(payload)
    changed["selection_manifest"]["windows"][0]["membership_content_hash"] = "sha256:" + "e" * 64
    with pytest.raises(CompositeEvidenceRefused, match="RESPONSE_CHANGED"):
        admit(changed, selection=selection)
    changed = deepcopy(payload)
    changed["periods"][0]["return_value"] = "0.02"
    with pytest.raises(CompositeEvidenceRefused, match="RESPONSE_CHANGED"):
        admit(changed, selection=selection)


def test_identity_refuses_even_when_caller_pins_foreign_response_hash():
    payload = calculated_example()
    selection = selection_for(payload)
    payload["composite_id"] = "foreign"
    selection = selection.model_copy(
        update={"response_digest": selection_for(payload).response_digest}
    )
    with pytest.raises(CompositeEvidenceRefused, match="IDENTITY_MISMATCH"):
        admit(payload, selection=selection)


@pytest.mark.parametrize(
    "defect,code",
    [
        ("missing-period", "PERIOD_MISSING"),
        ("missing-member", "MEMBER_COUNT_MISMATCH"),
        ("duplicate-member", "MEMBER_COUNT_MISMATCH"),
        ("wrong-fee", "PERIOD_IDENTITY_MISMATCH"),
        ("wrong-sequence", "PERIOD_IDENTITY_MISMATCH"),
        ("wrong-member-sequence", "MEMBER_IDENTITY_MISMATCH"),
        ("wrong-total", "CUMULATIVE_CONFLICT"),
        ("blocked-value", "BLOCKED_VALUE"),
        ("ready-null", "READY_EVIDENCE_MISSING"),
        ("missing-context", "FINANCIAL_CONTEXT_MISSING"),
    ],
)
def test_structural_population_and_context_fail_closed(defect, code):
    payload = calculated_example()
    period = payload["periods"][0]
    if defect == "missing-period":
        payload["periods"].pop(0)
    elif defect == "missing-member":
        period["member_contributions"].pop()
    elif defect == "duplicate-member":
        period["member_contributions"][1] = deepcopy(period["member_contributions"][0])
    elif defect == "wrong-fee":
        period["return_view"] = "GROSS"
    elif defect == "wrong-sequence":
        period["restatement_sequence"] = 2
    elif defect == "wrong-member-sequence":
        period["member_contributions"][0]["restatement_sequence"] = 2
    elif defect == "wrong-total":
        payload["cumulative_return"] = "0.01"
    elif defect == "blocked-value":
        period["status"] = "BLOCKED"
    elif defect == "ready-null":
        period["return_value"] = None
    elif defect == "missing-context":
        period["return_view"] = None
    with pytest.raises(CompositeEvidenceRefused, match=code):
        admit(payload)


def test_unavailable_is_preserved_without_claiming_ready():
    payload = calculated_example()
    period = payload["periods"][-1]
    period.update(
        status="BLOCKED",
        return_value=None,
        cumulative_return=None,
        member_count=0,
        member_contributions=[],
        reason_codes=["REQUIRED_PERIOD_UNAVAILABLE"],
    )
    payload.update(
        status="BLOCKED", cumulative_return=None, reason_codes=["REQUIRED_PERIOD_UNAVAILABLE"]
    )
    assert admit(payload)["source_response"] == payload


def test_pin_rejects_gaps_duplicates_latest_and_unbounded_vectors():
    selection = selection_for(calculated_example()).model_dump(mode="json")
    for defect in ("gap", "duplicate", "latest", "too-many"):
        invalid = deepcopy(selection)
        if defect == "gap":
            invalid["windows"][1]["period_start"] = "2026-02-02"
        elif defect == "duplicate":
            invalid["windows"][1]["materialization_id"] = invalid["windows"][0][
                "materialization_id"
            ]
        elif defect == "latest":
            invalid["restatement_sequence"] = "LATEST"
        else:
            invalid["windows"] *= 61
        with pytest.raises(ValidationError):
            CompositeReportSelection.model_validate(invalid)


def test_performance_request_has_explicit_ordered_ids_no_latest_selection():
    selection = selection_for(calculated_example())
    request = selection.performance_request()
    assert request["materialization_ids"] == [
        str(window.materialization_id) for window in selection.windows
    ]
    assert "restatement_sequence" not in request
    assert "tenant_id" not in request
    assert request["return_view"] == "NET_ACTUAL"
