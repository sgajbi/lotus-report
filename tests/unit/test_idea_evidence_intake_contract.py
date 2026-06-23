from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _load_validator() -> ModuleType:
    script_path = ROOT / "scripts" / "validate_idea_evidence_intake_contract.py"
    spec = importlib.util.spec_from_file_location(
        "validate_idea_evidence_intake_contract",
        script_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_idea_evidence_intake_contract_gate_passes_current_contract() -> None:
    module = _load_validator()

    assert module.validate_idea_evidence_intake_contract() == []


def test_idea_evidence_intake_contract_gate_cli_reports_success(capsys: Any) -> None:
    module = _load_validator()

    assert module.main() == 0

    assert "Idea evidence intake contract gate passed" in capsys.readouterr().out


def test_idea_evidence_intake_contract_blocks_premature_support(tmp_path: Path) -> None:
    module = _load_validator()
    contract = _contract_payload()
    contract["lifecycle_status"] = "active"
    contract["supportability_status"] = "supported"
    contract["route_existence_proven"] = True
    contract["materialization_proven"] = True
    contract["supported_feature_promoted"] = True
    contract_path = tmp_path / "contract.json"
    products_path = tmp_path / "products.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    products_path.write_text(json.dumps(_products_payload()), encoding="utf-8")

    errors = module.validate_idea_evidence_intake_contract(
        contract_path=contract_path,
        products_path=products_path,
    )

    assert "lifecycle_status must be planned" in errors
    assert "supportability_status must be not_certified" in errors
    assert "route_existence_proven must remain false until live proof exists" in errors
    assert "materialization_proven must remain false until live proof exists" in errors
    assert "supported_feature_promoted must remain false until live proof exists" in errors


def test_idea_evidence_intake_contract_blocks_boundary_drift(tmp_path: Path) -> None:
    module = _load_validator()
    contract = _contract_payload()
    contract["source_authority"]["report_materialization"] = "lotus-idea"
    contract["required_payload_fields"].append("client_name")
    contract["certification_blockers"] = []
    contract["non_proof_boundaries"] = ["Does not prove a live route."]
    contract_path = tmp_path / "contract.json"
    products_path = tmp_path / "products.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    products_path.write_text(json.dumps(_products_payload()), encoding="utf-8")

    errors = module.validate_idea_evidence_intake_contract(
        contract_path=contract_path,
        products_path=products_path,
    )

    assert "source_authority.report_materialization must be lotus-report" in errors
    assert any(
        "required_payload_fields must not include forbidden fields" in error for error in errors
    )
    assert any("certification_blockers missing" in error for error in errors)
    assert any("non_proof_boundaries must mention rendered document" in error for error in errors)


def test_idea_evidence_intake_contract_requires_idea_consumer_approval(
    tmp_path: Path,
) -> None:
    module = _load_validator()
    products = _products_payload()
    products["products"][0]["approved_consumers"] = ["lotus-gateway"]
    contract_path = tmp_path / "contract.json"
    products_path = tmp_path / "products.json"
    contract_path.write_text(json.dumps(_contract_payload()), encoding="utf-8")
    products_path.write_text(json.dumps(products), encoding="utf-8")

    errors = module.validate_idea_evidence_intake_contract(
        contract_path=contract_path,
        products_path=products_path,
    )

    assert (
        "ClientReportEvidencePack producer declaration must approve lotus-idea as a consumer"
        in errors
    )


def _contract_payload() -> dict[str, Any]:
    return json.loads(
        (
            ROOT
            / "contracts"
            / "idea-evidence-intake"
            / "lotus-report-idea-evidence-pack-intake.v1.json"
        ).read_text(encoding="utf-8")
    )


def _products_payload() -> dict[str, Any]:
    return json.loads(
        (ROOT / "contracts" / "domain-data-products" / "lotus-report-products.v1.json").read_text(
            encoding="utf-8"
        )
    )
