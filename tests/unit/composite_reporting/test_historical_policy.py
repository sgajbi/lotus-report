"""Pinned producer evidence, exact operation binding, and complete scalar custody."""

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.composite_reporting.admission import CompositeEvidenceRefused, response_digest
from app.composite_reporting.eligibility_admission import source_hash
from app.composite_reporting.historical_source import (
    require_operation,
    require_policy,
    validate_product,
)
from app.composite_reporting.historical_tables import build_historical_dataset, scalar_pointers
from app.composite_reporting.models import HistoricalEligibilitySelection

FIXTURES = Path(__file__).parents[2] / "fixtures" / "composite-historical-policy"
SCHEMAS = Path(__file__).parents[3] / "src" / "app" / "composite_reporting" / "historical_schemas"


def product(name, version):
    return json.loads((FIXTURES / f"CompositeMonthly{name}.{version}.controlled.json").read_bytes())


def selection(proposal):
    policy = proposal["policy_approval"]["proposal"]["policy"]
    return SimpleNamespace(
        **policy["scope"],
        reporting_currency=proposal["policy_approval"]["proposal"]["verification"]["mapping"][
            "reporting_currency"
        ],
    )


def evaluated_historical_example(version="v3"):
    proposal = product("EvaluationProposal", version)
    scope = selection(proposal)
    pin = {
        "product_version": version,
        "evidence_kind": "EVALUATED_ONLY",
        "month": proposal["evaluation"]["month"],
        "evaluation_revision": proposal["evaluation_revision"],
        "proposal_content_hash": proposal["content_hash"],
        "proposal_response_digest": response_digest(proposal),
        "source_cut_id": proposal["observations"]["source_cut_id"],
        "parent_membership_revision": proposal["parent_membership_revision"],
        "parent_membership_content_hash": proposal["parent_membership_content_hash"],
    }
    receipts = []
    if version == "v4":
        root = product("EligibilityPublicationReceipt", "v3")
        receipts.append(root)
        pin["lineage_receipts"] = [
            {
                "product_version": "v3",
                "evaluation_revision": root["approval"]["proposal"]["evaluation_revision"],
                "approval_content_hash": root["approval"]["content_hash"],
                "receipt_content_hash": root["content_hash"],
                "receipt_response_digest": response_digest(root),
            }
        ]
    selected = HistoricalEligibilitySelection.model_validate(
        {
            "selection_version": "v3",
            "tenant_id": scope.tenant_id,
            "composite_id": scope.composite_id,
            "definition_version": scope.definition_version,
            "reporting_currency": scope.reporting_currency,
            "period_start": "2026-09-01",
            "period_end": "2026-09-30",
            "months": [pin],
        }
    )
    return selected, [
        {
            "evidence_kind": "EVALUATED_ONLY",
            "proposal": proposal,
            "lineage_receipts": receipts,
            "response_digests": {"proposal": response_digest(proposal)},
        }
    ]


@pytest.mark.parametrize("version", ["v3", "v4"])
def test_real_evaluated_root_and_correction_projection(version):
    selected, months = evaluated_historical_example(version)
    data = build_historical_dataset(selected, months)
    assert data["source_months"] == months
    assert len(data["tables"]) == 10
    assert data["tables"][-1]["table_id"] == "PolicyAdmission"


def test_exact_producer_schema_and_example_assets():
    manifest = json.loads((FIXTURES / "manifest.json").read_bytes())
    assert (
        sha256((FIXTURES / "manifest.json").read_bytes()).hexdigest()
        == "42f303700e1699774df2a8babd34eb32d79aee3a261ddb7f7b6ceed141834a2e"
    )
    for name, digest in manifest["raw_file_sha256"].items():
        path = (SCHEMAS if ".schema." in name else FIXTURES) / name
        raw = path.read_bytes()
        assert sha256(raw).hexdigest() == digest
        canonical = json.dumps(
            json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        assert (
            "sha256:" + sha256(canonical).hexdigest() == manifest["canonical_content_digests"][name]
        )


@pytest.mark.parametrize("version", ["v3", "v4"])
def test_pinned_proposal_and_approval_operations(version):
    proposal = product("EvaluationProposal", version)
    approval = product("EvaluationApproval", version)
    for value in [proposal, approval, product("EligibilityPublicationReceipt", version)]:
        validate_product(value)
    require_policy(selection(proposal), proposal["evaluation"]["month"], proposal)
    require_operation(approval, proposal, "EVALUATION_APPROVAL")


@pytest.mark.parametrize(
    "axis", ["actor_id", "revision", "intent_digest", "operation", "requested_at", "scope"]
)
def test_rehashed_operation_substitution_refused(axis):
    proposal = product("EvaluationProposal", "v3")
    changed = deepcopy(proposal)
    proof = changed["operation_verification"]
    proof["request"][axis] = (
        {**proof["request"][axis], "tenant_id": "foreign"} if axis == "scope" else "substituted"
    )
    proof["content_hash"] = source_hash(
        {key: value for key, value in proof.items() if key != "content_hash"}
    )
    with pytest.raises(CompositeEvidenceRefused):
        require_operation(changed, changed, "EVALUATION_PROPOSAL")


def test_scalar_recipe_preserves_null_order_and_escaped_keys():
    assert scalar_pointers({"z": None, "a/b": [1, {"~": False}]}, "/proof") == [
        "/proof/a~1b/0",
        "/proof/a~1b/1/~0",
        "/proof/z",
    ]


def test_policy_table_capacity_refuses_complete_oversize_projection():
    from app.composite_reporting.historical_tables import historical_tables
    from tests.unit.composite_reporting.historical_examples import graph_example

    selected, months = graph_example("v2", "correction-3", True)
    data = build_historical_dataset(selected, months)
    assert len(data["tables"]) == 10
    # Isolated projection-capacity input; repeated source months are deliberately
    # not claimed as an admitted multi-month selector or producer source graph.
    data["source_months"] *= 20
    data["selection"]["months"] *= 20
    with pytest.raises(CompositeEvidenceRefused, match="HISTORICAL_TABLE_CAPACITY_EXCEEDED"):
        historical_tables(data)


@pytest.mark.parametrize(
    "value", [None, 0, "v3", {}, {"product_version": "v2", "evidence_kind": "PUBLISHED"}]
)
def test_invalid_month_variants_are_validation_errors(value):
    from pydantic import ValidationError

    selected, _ = evaluated_historical_example()
    wire = selected.model_dump(mode="json")
    wire["months"] = [value]
    with pytest.raises(ValidationError):
        HistoricalEligibilitySelection.model_validate(wire)


@pytest.mark.parametrize("definition", ["v1", "v2"])
@pytest.mark.parametrize("step", ["root", "correction-2", "correction-3"])
@pytest.mark.parametrize("published", [False, True])
def test_complete_producer_graph(definition, step, published):
    from jsonschema import Draft202012Validator

    from app.composite_reporting.historical_contract import composite_historical_report_schema
    from tests.unit.composite_reporting.historical_examples import graph_example

    selected, months = graph_example(definition, step, published)
    data = build_historical_dataset(selected, months)
    Draft202012Validator(composite_historical_report_schema()).validate(data)
    assert data["source_months"] == months
    from app.composite_reporting.historical_contract import CompositeHistoricalReportData

    reordered = json.loads(json.dumps(data, sort_keys=True))
    assert CompositeHistoricalReportData.model_validate(reordered).model_dump(mode="json") == data


@pytest.mark.parametrize(
    "mutation", ["omit_root", "root_version", "proof_bytes", "projection", "tenant", "tables"]
)
def test_selected_historical_custody_cannot_be_substituted(mutation):
    from pydantic import ValidationError

    from app.composite_reporting.historical_contract import CompositeHistoricalReportData
    from tests.unit.composite_reporting.historical_examples import graph_example

    selected, months = graph_example("v2", "correction-3", True)
    data = build_historical_dataset(selected, months)
    if mutation == "omit_root":
        data["source_months"][0]["lineage_receipts"].pop()
    elif mutation == "root_version":
        data["selection"]["months"][0]["lineage_receipts"][-1]["product_version"] = "v4"
    elif mutation == "proof_bytes":
        data["source_months"][0]["receipt"]["approval"]["operation_verification"]["mapping"][
            "raw_original_base64"
        ] = "YQ=="
    elif mutation == "projection":
        data["source_months"][0]["membership"]["decisions"][0]["status"] = "PENDING_REVIEW"
    elif mutation == "tenant":
        data["selection"]["tenant_id"] = "foreign"
    else:
        data["tables"][-1]["rows"].pop()
    with pytest.raises((CompositeEvidenceRefused, ValidationError)):
        CompositeHistoricalReportData.model_validate(data)


def test_graph_assets_are_exact_frozen_producer_bytes():
    from tests.unit.composite_reporting.historical_examples import GRAPHS

    raw = (GRAPHS / "manifest.json").read_bytes()
    assert (
        sha256(raw).hexdigest()
        == "4b91c97835bcb2b00d095c0ce02009d9b519e81847966ac07e2557d64d644c57"
    )
    manifest = json.loads(raw)
    for name, digest in manifest["raw_file_sha256"].items():
        assert sha256((GRAPHS / name).read_bytes()).hexdigest() == digest


def test_published_v7_schema_matches_generator():
    from app.composite_reporting.historical_contract import composite_historical_report_schema

    assert (
        json.loads(
            (Path(__file__).parents[3] / "contracts/composite_review.v7.schema.json").read_bytes()
        )
        == composite_historical_report_schema()
    )


@pytest.mark.parametrize("published", [False, True])
def test_actual_two_month_graph_global_ordinals_and_retained_projection(published):
    from app.composite_reporting.historical_contract import CompositeHistoricalReportData
    from tests.unit.composite_reporting.historical_examples import (
        GRAPHS,
        two_month_historical_example,
    )

    folder = GRAPHS.parent / "composite-historical-two-month"
    raw = (folder / "manifest.json").read_bytes()
    assert (
        sha256(raw).hexdigest()
        == "0ff8e4ff783412abebff1dd329e1d2e1dd24a4afe4bbc354fc3df90d3d109d6f"
    )
    for name, digest in json.loads(raw)["raw_file_sha256"].items():
        assert sha256((folder / name).read_bytes()).hexdigest() == digest
    selected, months = two_month_historical_example(published)
    data = build_historical_dataset(selected, months)
    assert data["source_months"] == months
    for table, prefix in [(data["tables"][-2], "a"), (data["tables"][-1], "p")]:
        rows = table["rows"]
        boundary = next(index for index, row in enumerate(rows) if row["row_id"].startswith("m1:"))
        assert rows[boundary]["row_id"] == f"m1:{prefix}{boundary}"
        assert all(row["row_id"].endswith(f":{prefix}{index}") for index, row in enumerate(rows))
    reordered = json.loads(json.dumps(data, sort_keys=True))
    assert CompositeHistoricalReportData.model_validate(reordered).model_dump(mode="json") == data


@pytest.mark.parametrize(
    "mutation",
    ["raw_bytes", "signer", "key", "signature", "revocation", "status", "expiry", "admission"],
)
def test_rehashed_recorded_proof_refusals(mutation):
    from app.composite_reporting.historical_source import require_proof

    proposal = product("EvaluationProposal", "v3")
    proof = deepcopy(proposal["operation_verification"])
    policy = proposal["policy_approval"]["proposal"]
    if mutation == "raw_bytes":
        proof["mapping"]["raw_original_base64"] = "Y2hhbmdlZA=="
        proof["mapping"]["content_hash"] = source_hash(proof["mapping"])
    elif mutation == "signer":
        proof["signer_principal_id"] = proof["verifier_principal_id"]
    elif mutation == "key":
        proof["signer_key_digest"] = proof["verifier_key_digest"]
    elif mutation == "signature":
        proof["verifier_credential"] = "YQ=="
    elif mutation == "revocation":
        proof["current_revocation_status"] = "REVOKED"
    elif mutation == "status":
        proof["original_signature_status"] = "UNVERIFIED"
    elif mutation == "expiry":
        proof["expires_at"] = "2026-10-10T23:59:59Z"
    else:
        proof["admitted_at"] = "2026-10-10T00:00:00Z"
    proof["content_hash"] = source_hash(proof)
    with pytest.raises(CompositeEvidenceRefused):
        require_proof(
            proof,
            policy,
            operation="EVALUATION_PROPOSAL",
            actor=proposal["proposed_by"],
            revision=proposal["evaluation_revision"],
            at=proposal["proposed_at"],
            intent=proof["request"]["intent_digest"],
        )


def test_complete_provenance_includes_each_original_and_operation_proof():
    from app.composite_reporting.historical_tables import provenance_paths
    from app.composite_reporting.table_contract import resolve_source_pointer
    from tests.unit.composite_reporting.historical_examples import graph_example

    selected, months = graph_example("v2", "correction-3", True)
    data = build_historical_dataset(selected, months)
    rows = data["tables"][-1]["rows"]
    pointers = provenance_paths(data)
    assert len(rows) == len(pointers)
    assert [row["row_id"] for row in rows] == [f"m0:p{index}" for index in range(len(rows))]
    for row, (_, role, pointer) in zip(rows, pointers, strict=True):
        assert row["cells"]["value"]["source_pointer"] == pointer
        assert row["cells"]["evidence_role"]["canonical_value"] == role
        value = resolve_source_pointer(data, pointer)
        assert value is None or row["cells"]["value"]["canonical_value"] is not None
    raw_paths = [pointer for _, _, pointer in pointers if pointer.endswith("/raw_original_base64")]
    signature_paths = [
        pointer for _, _, pointer in pointers if pointer.endswith("/verifier_credential")
    ]
    assert len(raw_paths) == 12  # Four proof occurrences per selected/retained published month.
    assert len(signature_paths) == 12
    assert all(resolve_source_pointer(data, pointer) for pointer in raw_paths + signature_paths)


@pytest.mark.asyncio
async def test_historical_default_disabled_before_source_io(monkeypatch):
    from app.composite_reporting.source_capture import CompositeInputProvider
    from app.config import settings
    from app.reporting_lineage.capture_service import PortfolioReviewInputCaptureError
    from tests.unit.composite_reporting.test_eligibility_capture import eligibility_job

    selected, _ = evaluated_historical_example()
    calls = []

    class Source:
        async def read_eligibility(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("Disabled consumer must not read upstream")

    monkeypatch.setattr(settings, "composite_historical_policy_enabled", False)
    with pytest.raises(PortfolioReviewInputCaptureError) as caught:
        await CompositeInputProvider(manage_client=Source()).collect_for_job(
            eligibility_job(selected)
        )
    assert "HISTORICAL_POLICY_RELEASE_NOT_ADMITTED" in str(caught.value.original_error)
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("published", [False, True])
async def test_capture_exact_new_versions_and_retained_receipts(monkeypatch, published):
    from app.composite_reporting.source_capture import CompositeInputProvider
    from app.config import settings
    from tests.unit.composite_reporting.historical_examples import graph_example
    from tests.unit.composite_reporting.test_eligibility_capture import eligibility_job

    selected, months = graph_example("v2", "correction-3", published)
    month = months[0]
    calls = []

    class Source:
        async def read_eligibility(self, **kwargs):
            calls.append(kwargs)
            binding, path = kwargs["binding"], kwargs["endpoint"]
            assert kwargs["admitted_tenant_id"] == selected.tenant_id
            if binding is not None:
                receipts = [*month["lineage_receipts"], *([month["receipt"]] if published else [])]
                value = next(
                    row for row in receipts if row["approval"]["content_hash"] == binding["digest"]
                )
                assert binding["product_version"] == value["approval"]["product_version"]
            elif "/evaluations/" in path:
                value = month["proposal"]
            elif "/publications/" in path:
                value = next(
                    row
                    for row in [month["publication"], month["parent_publication"]]
                    if str(row["sequence"]) == path.rsplit("/", 1)[1]
                )
            elif "/universe-attestations/" in path:
                value = month["universe"]
            else:
                value = next(
                    row
                    for row in [month["membership"], month["parent_membership"]]
                    if path.endswith("/" + row["membership_revision"])
                )
            return 200, deepcopy(value)

    monkeypatch.setattr(settings, "composite_historical_policy_enabled", True)
    job = eligibility_job(selected).model_copy(
        update={
            "accepted_document_contract": {"input_snapshot_contract_version": "composite_review.v7"}
        }
    )
    capture = await CompositeInputProvider(manage_client=Source()).collect_for_job(job)
    assert capture.snapshot_payload == build_historical_dataset(selected, months)
    assert len(calls) == (8 if published else 3)
