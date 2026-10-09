"""Actual source wire, semantic refusal and immutable projection for pooled v5."""

import gzip
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.composite_reporting.admission import CompositeEvidenceRefused, response_digest
from app.composite_reporting.models import CompositeReviewJobRequest, PooledAnalysisSelection
from app.composite_reporting.pooled_contract import (
    CompositePooledReportData,
    admit_pooled_response,
    composite_pooled_report_schema,
)
from app.composite_reporting.pooled_tables import build_pooled_dataset


def test_complete_projection_budget_refuses_large_source_without_truncation():
    original = pair()[0]
    original["controlled_padding"] = "x" * 4_300_000
    with pytest.raises(CompositeEvidenceRefused, match="DATASET_CAPACITY_EXCEEDED"):
        build_pooled_dataset(
            selection=selection(original),
            admitted_tenant_id=original["observation"]["tenant_id"],
            status_code=200,
            payload=original,
            predecessor=None,
        )
    assert len(original["controlled_padding"]) == 4_300_000


def pair():
    return json.loads(
        gzip.decompress(
            (Path(__file__).parent / "source_fixtures/pooled-mwr-98a4bef-pair.json.gz").read_bytes()
        )
    )


def selection(payload, predecessor=None):
    observation = payload["observation"]
    bundle = observation["source_bundle"]
    policy = bundle["policy"]
    return PooledAnalysisSelection.model_validate(
        {
            "tenant_id": observation["tenant_id"],
            "composite_id": payload["composite_id"],
            "calculation_id": payload["calculation_id"],
            "schema_version": payload["schema_version"],
            "metric_id": payload["metric_id"],
            "method": payload["method"],
            "period_start": observation["period_start"],
            "period_end": observation["period_end"],
            "reporting_currency": observation["reporting_currency"],
            "return_view": policy["return_view"],
            "fee_basis": policy["fee_basis"],
            "policy_binding_id": policy["binding_id"],
            "policy_content_hash": policy["content_hash"],
            "day_count_basis": policy["day_count_basis"],
            "fallback_policy": policy["fallback_policy"],
            "engine_version": payload["calculation_engine_version"],
            "source_manifest_id": observation["source_manifest_id"],
            "input_manifest_digest": payload["input_manifest_digest"],
            "source_bundle_digest": response_digest(bundle),
            "response_digest": response_digest(payload),
            "expected_portfolio_ids": bundle["expected_portfolio_ids"],
            "source_pins": bundle["source_pins"],
            "correction_of_calculation_id": payload["correction_of_calculation_id"],
            "predecessor_response_digest": None
            if predecessor is None
            else response_digest(predecessor),
        }
    )


def dataset(payload, predecessor=None, pin=None):
    pin = pin or selection(payload, predecessor)
    return build_pooled_dataset(
        selection=pin,
        admitted_tenant_id=pin.tenant_id,
        status_code=200,
        payload=payload,
        predecessor=predecessor,
    )


def test_actual_original_correction_wire_is_retained_without_a_report_solver():
    original, corrected = pair()
    for payload, parent in ((original, None), (corrected, original)):
        data = dataset(payload, parent)
        assert data["source_response"] == payload
        assert data["predecessor_source_response"] == parent
        assert data["contract_version"] == "composite_review.v5"
        assert data["publication_state"] == "NOT_ATTESTED"
        assert (
            data["tables"][1]["rows"][0]["cells"]["return_value"]["canonical_value"]
            == (payload["outcome"]["return_value"])
        )
        assert CompositePooledReportData.model_validate(data)
    assert original["outcome"]["return_value"] != corrected["outcome"]["return_value"]


def test_standalone_schema_and_examples_match_the_admitted_original_and_correction():
    root = Path(__file__).resolve().parents[3]
    schema = composite_pooled_report_schema()
    assert json.loads((root / "contracts/composite_review.v5.schema.json").read_bytes()) == schema
    Draft202012Validator.check_schema(schema)
    original, corrected = pair()
    for name, payload, parent in (("original", original, None), ("corrected", corrected, original)):
        expected = dataset(payload, parent)
        example = json.loads(
            (root / f"contracts/examples/composite-review.v5.{name}.json").read_bytes()
        )
        assert example == expected
        Draft202012Validator(schema).validate(example)


@pytest.mark.parametrize(
    "case", ["ambiguous", "elected_fallback", "one_sided", "work_limit", "zero"]
)
def test_actual_registered_solver_disposition_bodies_are_preserved(case):
    fixture = json.loads(
        gzip.decompress(
            (
                Path(__file__).parent / "source_fixtures/pooled-mwr-98a4bef-dispositions.json.gz"
            ).read_bytes()
        )
    )
    node = next(name for name in fixture["cases"] if name.endswith(f"[{case}]"))
    responses = fixture["cases"][node]
    payload = responses[2]["response_body"]
    data = dataset(payload)
    assert data["source_response"] == payload
    assert (
        data["source_response"]["outcome"]["original_solver_result"]
        == (payload["outcome"]["original_solver_result"])
    )
    if case == "elected_fallback":
        assert payload["outcome"]["availability"] == "FALLBACK_ANALYSIS"
        assert payload["outcome"]["actual_method"] == "MODIFIED_DIETZ"
        assert payload["outcome"]["return_value"] is not None
    else:
        assert payload["outcome"]["availability"] == "NOT_CALCULABLE"
        assert payload["outcome"]["return_value"] is None


@pytest.mark.parametrize(
    "path,value,code",
    [
        (("calculation_engine_version",), "other-engine", "SOURCE_IDENTITY"),
        (
            ("correction_of_calculation_id",),
            "6a7fd1c2-31ba-4597-9422-36c4cee79701",
            "SOURCE_IDENTITY",
        ),
        (("observation", "tenant_id"), "foreign", "OBSERVATION_IDENTITY"),
        (("observation", "reporting_currency"), "EUR", "OBSERVATION_IDENTITY"),
        (("observation", "period_end"), "2026-01-02", "OBSERVATION_IDENTITY"),
        (("observation", "input_manifest_digest"), "sha256:" + "f" * 64, "OBSERVATION_IDENTITY"),
        (("observation", "source_bundle", "policy", "return_view"), "NET_ACTUAL", "POLICY"),
        (("observation", "source_bundle", "policy", "fee_basis"), "OTHER", "POLICY"),
        (("observation", "source_bundle", "population_complete"), False, "POPULATION"),
        (("observation", "source_bundle", "expected_population_count"), 3, "POPULATION"),
        (("outcome", "diagnostics", "day_count_basis"), "BUS/252", "SOLVER_INTERVAL"),
        (("outcome", "diagnostics", "convergence", "converged"), False, "XIRR_NOT_QUALIFIED"),
        (
            ("outcome", "diagnostics", "convergence", "root_count_detected"),
            True,
            "XIRR_NOT_QUALIFIED",
        ),
        (
            ("outcome", "diagnostics", "convergence", "uniqueness_supported"),
            False,
            "XIRR_NOT_QUALIFIED",
        ),
    ],
)
def test_semantic_mixed_wire_is_refused_even_when_response_digest_is_repinned(path, value, code):
    original = pair()[0]
    pin = selection(original)
    payload = deepcopy(original)
    current = payload
    for component in path[:-1]:
        current = current[component]
    current[path[-1]] = value
    pin = pin.model_copy(
        update={
            "response_digest": response_digest(payload),
            "source_bundle_digest": response_digest(payload["observation"]["source_bundle"]),
        }
    )
    with pytest.raises(ValueError, match=code):
        dataset(payload, pin=pin)


@pytest.mark.parametrize("status", [202, 401, 403, 404, 409, 500])
def test_pending_and_operational_refusals_never_become_financial_data(status):
    original = pair()[0]
    pin = selection(original)
    with pytest.raises(CompositeEvidenceRefused, match="SOURCE_UNAVAILABLE"):
        admit_pooled_response(
            selection=pin,
            admitted_tenant_id=pin.tenant_id,
            status_code=status,
            payload={"status": "accepted"},
        )


def test_tenant_authority_and_response_digest_are_independent():
    original = pair()[0]
    pin = selection(original)
    with pytest.raises(CompositeEvidenceRefused, match="TENANT_MISMATCH"):
        admit_pooled_response(
            selection=pin, admitted_tenant_id="foreign", status_code=200, payload=original
        )
    changed = deepcopy(original)
    changed["outcome"]["return_value"] = "99"
    with pytest.raises(CompositeEvidenceRefused, match="RESPONSE_CHANGED"):
        dataset(changed, pin=pin)


def test_source_body_and_member_money_scope_cannot_borrow_valid_other_pins():
    original = pair()[0]
    pin = selection(original)
    for mutate, code in (
        (
            lambda b: b["raw_source_bodies"]["money"].update(controlled_revision="other"),
            "SOURCE_BODY",
        ),
        (lambda b: b["valuations"][0].update(portfolio_id="foreign"), "MONEY_SCOPE"),
        (lambda b: b["valuations"][0].update(currency="EUR"), "MONEY_SCOPE"),
        (lambda b: b["flow_coverage"][0].update(complete=False), "FLOW_COVERAGE"),
    ):
        payload = deepcopy(original)
        mutate(payload["observation"]["source_bundle"])
        changed_pin = pin.model_copy(
            update={
                "response_digest": response_digest(payload),
                "source_bundle_digest": response_digest(payload["observation"]["source_bundle"]),
            }
        )
        with pytest.raises(CompositeEvidenceRefused, match=code):
            dataset(payload, pin=changed_pin)


def test_rejected_dietz_diagnostics_never_promote_a_return():
    payload = pair()[0]
    outcome = payload["outcome"]
    outcome.update(
        availability="NOT_CALCULABLE",
        actual_method="MODIFIED_DIETZ",
        return_value=None,
        annualized_return=None,
        holding_period_return=None,
        reason_codes=["AMBIGUOUS_ROOT"],
    )
    outcome["original_solver_result"].update(method="MODIFIED_DIETZ", mwr=42.0)
    data = dataset(payload)
    assert data["tables"][1]["rows"][0]["cells"]["return_value"]["canonical_value"] is None
    outcome["return_value"] = "0.42"
    with pytest.raises(CompositeEvidenceRefused, match="REJECTED_RETURN_PRESENT"):
        dataset(payload)


def test_fallback_requires_explicit_policy_and_source_classification():
    payload = pair()[0]
    payload["outcome"].update(
        availability="FALLBACK_ANALYSIS",
        actual_method="MODIFIED_DIETZ",
        reason_codes=["XIRR_FAILED"],
    )
    payload["outcome"]["diagnostics"].update(fallback_from="XIRR", fallback_reason="XIRR_FAILED")
    payload["outcome"]["original_solver_result"].update(
        status="FALLBACK_USED", method="MODIFIED_DIETZ"
    )
    with pytest.raises(CompositeEvidenceRefused, match="FALLBACK_NOT_ELECTED"):
        dataset(payload)
    payload["observation"]["source_bundle"]["policy"]["fallback_policy"] = "ALLOW_MODIFIED_DIETZ"
    assert dataset(payload)["source_response"]["outcome"]["actual_method"] == "MODIFIED_DIETZ"


def test_sparse_numerical_domain_failure_preserves_nulls_and_missing_diagnostics():
    payload = pair()[0]
    payload["outcome"].update(
        availability="NOT_CALCULABLE",
        actual_method="XIRR",
        return_value=None,
        annualized_return=None,
        holding_period_return=None,
        reason_codes=["NUMERICAL_DOMAIN_UNSUPPORTED"],
        diagnostics={
            "actual_interval_start": payload["observation"]["period_start"],
            "actual_interval_end": payload["observation"]["period_end"],
            "root_precision": "FLOAT64",
            "actual_algorithm": None,
            "projection_failure": "Controlled unsupported projection",
            "solver_controls": {},
        },
        original_solver_result={"error_type": "NumericalDomainError", "message": "Controlled"},
    )
    data = dataset(payload)
    assert data["source_response"] == payload
    assert "day_count_basis" not in data["source_response"]["outcome"]["diagnostics"]


def test_complete_layout_refuses_omitted_data_forged_values_and_display_policy():
    good = dataset(pair()[0])
    for mutate in (
        lambda d: d["tables"].pop(),
        lambda d: d["tables"][1]["rows"][0]["cells"]["return_value"].update(canonical_value="99"),
        lambda d: d["tables"][1]["columns"][-1].update(display_conversion="IDENTITY"),
        lambda d: d["tables"][-1]["rows"].pop(),
    ):
        data = deepcopy(good)
        mutate(data)
        with pytest.raises(ValueError, match="TABLE_LAYOUT"):
            CompositePooledReportData.model_validate(data)


def test_correction_parent_is_required_and_cannot_cross_tenant_or_calculation():
    original, corrected = pair()
    pin = selection(corrected, original)
    for parent in (None, corrected, {**original, "calculation_id": corrected["calculation_id"]}):
        with pytest.raises(CompositeEvidenceRefused, match="PREDECESSOR"):
            dataset(corrected, parent, pin)


def test_typed_primary_is_exclusive_and_cannot_be_smuggled_through_options():
    pin = selection(pair()[0]).model_dump(mode="json")
    request = CompositeReviewJobRequest(pooled_selection=pin)
    assert request.capture_options() == {"composite_pooled_selection": pin}
    with pytest.raises(ValueError, match="typed pooled_selection"):
        CompositeReviewJobRequest(pooled_selection=pin, options={"composite_pooled_selection": pin})
    with pytest.raises(ValueError):
        CompositeReviewJobRequest(pooled_selection=pin, source_products=[])


def test_jsonb_object_order_cannot_change_pooled_evidence_rows():
    data = dataset(pair()[0])

    def reverse_objects(value):
        if isinstance(value, dict):
            return {key: reverse_objects(value[key]) for key in reversed(list(value))}
        if isinstance(value, list):
            return [reverse_objects(child) for child in value]
        return value

    reordered = reverse_objects(data)
    assert CompositePooledReportData.model_validate(reordered)


@pytest.mark.parametrize(
    "field,value",
    [
        ("residual", float("nan")),
        ("gross_cash_flow_scale", 0),
        ("rate_upper_bound", -2),
        ("iterations", 201),
    ],
)
def test_nonfinite_or_inconsistent_available_solver_controls_refuse(field, value):
    payload = pair()[0]
    payload["outcome"]["diagnostics"]["convergence"][field] = value
    with pytest.raises(ValueError):
        dataset(payload)


def test_not_calculable_zero_flow_projection_remains_an_explicit_null_outcome():
    payload = pair()[0]
    payload["observation"]["investor_cash_flows"] = []
    payload["outcome"].update(
        availability="NOT_CALCULABLE",
        return_value=None,
        annualized_return=None,
        holding_period_return=None,
        reason_codes=["ZERO_CASH_FLOWS"],
    )
    # An empty sourced cash-flow list is not a fabricated zero-flow row.
    # The complete raw evidence retains the empty list explicitly.
    data = dataset(payload)
    assert all(table["table_id"] != "InvestorCashFlows" for table in data["tables"])
    evidence = next(table for table in data["tables"] if table["table_id"] == "SourceEvidence")
    assert any(
        row["cells"]["value"]["source_pointer"]
        == "/source_response/observation/investor_cash_flows"
        and row["cells"]["value"]["canonical_value"] == "[]"
        for row in evidence["rows"]
    )
