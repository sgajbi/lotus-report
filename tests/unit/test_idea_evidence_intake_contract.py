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
    contract["supportability_status"] = "supported"
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

    assert "supportability_status must be not_certified" in errors
    assert "materialization_proven must remain false until materialization proof exists" in errors
    assert (
        "supported_feature_promoted must remain false until materialization proof exists" in errors
    )


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
        "required_payload_fields must not include forbidden sensitive fragments" in error
        for error in errors
    )
    assert any("certification_blockers missing" in error for error in errors)
    assert any("non_proof_boundaries must mention rendered document" in error for error in errors)


def test_idea_evidence_intake_contract_requires_current_live_route(tmp_path: Path) -> None:
    module = _load_validator()
    contract = _contract_payload()
    contract["route_existence_proven"] = False
    contract["target_route"] = "planned:lotus-report-idea-evidence-pack-intake"
    contract_path = tmp_path / "contract.json"
    products_path = tmp_path / "products.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    products_path.write_text(json.dumps(_products_payload()), encoding="utf-8")

    errors = module.validate_idea_evidence_intake_contract(
        contract_path=contract_path,
        products_path=products_path,
    )

    assert "route_existence_proven must be true for the implemented intake route" in errors
    assert "target_route must be POST /reports/idea-evidence-packs" in errors


def test_idea_evidence_intake_contract_blocks_camel_case_required_fields(
    tmp_path: Path,
) -> None:
    module = _load_validator()
    contract = _contract_payload()
    contract["required_payload_fields"].append("reportEvidencePackId")
    contract_path = tmp_path / "contract.json"
    products_path = tmp_path / "products.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    products_path.write_text(json.dumps(_products_payload()), encoding="utf-8")

    errors = module.validate_idea_evidence_intake_contract(
        contract_path=contract_path,
        products_path=products_path,
    )

    assert any("canonical snake_case" in error for error in errors)


def test_idea_evidence_intake_contract_blocks_sensitive_field_fragments(
    tmp_path: Path,
) -> None:
    module = _load_validator()
    contract = _contract_payload()
    contract["required_payload_fields"].extend(
        [
            "primary_client_name",
            "raw_provider_response_json",
        ]
    )
    contract_path = tmp_path / "contract.json"
    products_path = tmp_path / "products.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    products_path.write_text(json.dumps(_products_payload()), encoding="utf-8")

    errors = module.validate_idea_evidence_intake_contract(
        contract_path=contract_path,
        products_path=products_path,
    )

    assert any(
        "primary_client_name" in error and "raw_provider_response_json" in error for error in errors
    )


def test_idea_evidence_intake_contract_requires_idea_producer_authority(
    tmp_path: Path,
) -> None:
    module = _load_validator()
    contract = _contract_payload()
    contract["approved_producer_repository"] = "lotus-report"
    contract["approved_producer_product"] = "lotus-report:ClientReportEvidencePack:v1"
    contract_path = tmp_path / "contract.json"
    products_path = tmp_path / "products.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    products_path.write_text(json.dumps(_products_payload()), encoding="utf-8")

    errors = module.validate_idea_evidence_intake_contract(
        contract_path=contract_path,
        products_path=products_path,
    )

    assert "approved_producer_repository must match source_authority.idea_evidence" in errors
    assert "approved_producer_product must identify a lotus-idea evidence product" in errors


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
