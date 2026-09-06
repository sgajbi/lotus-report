from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def test_idea_evidence_materialization_contract_gate_passes_current_contract() -> None:
    module = _load_validator()

    assert module.validate_idea_evidence_materialization_contract() == []


def test_idea_evidence_materialization_contract_blocks_publication_drift(
    tmp_path: Path,
) -> None:
    module = _load_validator()
    contract_path = (
        ROOT
        / "contracts"
        / "idea-evidence-materialization"
        / "lotus-report-idea-evidence-pack-materialization.v1.json"
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["client_publication_authority_granted"] = True
    drifted = tmp_path / "contract.json"
    drifted.write_text(json.dumps(contract), encoding="utf-8")

    errors = module.validate_idea_evidence_materialization_contract(drifted)

    assert "client_publication_authority_granted must be False" in errors


def test_idea_evidence_materialization_contract_blocks_authority_drift(
    tmp_path: Path,
) -> None:
    module = _load_validator()
    contract_path = (
        ROOT
        / "contracts"
        / "idea-evidence-materialization"
        / "lotus-report-idea-evidence-pack-materialization.v1.json"
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["source_authority"]["rendering"] = "lotus-report"
    drifted = tmp_path / "contract.json"
    drifted.write_text(json.dumps(contract), encoding="utf-8")

    errors = module.validate_idea_evidence_materialization_contract(drifted)

    assert "source_authority.rendering must be lotus-render" in errors


def test_idea_evidence_materialization_contract_requires_response_receipt_fields(
    tmp_path: Path,
) -> None:
    module = _load_validator()
    contract_path = (
        ROOT
        / "contracts"
        / "idea-evidence-materialization"
        / "lotus-report-idea-evidence-pack-materialization.v1.json"
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["response_fields"].remove("report_package_identity")
    contract["required_nested_response_fields"]["report_package_identity"].remove(
        "evidence_content_fingerprint"
    )
    drifted = tmp_path / "contract.json"
    drifted.write_text(json.dumps(contract), encoding="utf-8")

    errors = module.validate_idea_evidence_materialization_contract(drifted)

    assert "response_fields missing: report_package_identity" in errors
    assert (
        "required_nested_response_fields.report_package_identity missing: "
        "evidence_content_fingerprint"
    ) in errors


def test_idea_evidence_materialization_contract_requires_exact_read_only_recovery(
    tmp_path: Path,
) -> None:
    module = _load_validator()
    contract_path = (
        ROOT
        / "contracts"
        / "idea-evidence-materialization"
        / "lotus-report-idea-evidence-pack-materialization.v1.json"
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["recovery"]["tenant_scoped"] = False
    contract["recovery"]["retries_materialization"] = True
    contract["recovery"]["owner_version_source"] = "mutable_job_status"
    contract["recovery"]["owner_history_policy"] = "retention_may_delete"
    contract["recovery"]["exact_replay_preserves_owner_version"] = False
    contract["recovery"]["required_query_fields"].remove("conversionIntentId")
    drifted = tmp_path / "contract.json"
    drifted.write_text(json.dumps(contract), encoding="utf-8")

    errors = module.validate_idea_evidence_materialization_contract(drifted)

    assert "recovery.tenant_scoped must be True" in errors
    assert "recovery.retries_materialization must be False" in errors
    assert "recovery.owner_version_source must be 'append_only_report_status_event_count'" in errors
    assert "recovery.owner_history_policy must be 'append_only_no_delete'" in errors
    assert "recovery.exact_replay_preserves_owner_version must be True" in errors
    assert "recovery.required_query_fields missing: conversionIntentId" in errors


def test_idea_evidence_materialization_contract_requires_owner_version(
    tmp_path: Path,
) -> None:
    module = _load_validator()
    contract_path = (
        ROOT
        / "contracts"
        / "idea-evidence-materialization"
        / "lotus-report-idea-evidence-pack-materialization.v1.json"
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["response_fields"].remove("source_event_version")

    drifted = tmp_path / "contract.json"
    drifted.write_text(json.dumps(contract), encoding="utf-8")
    errors = module.validate_idea_evidence_materialization_contract(drifted)

    assert "response_fields missing: source_event_version" in errors


def test_report_status_event_history_remains_append_only() -> None:
    deletion = re.compile(r"\bDELETE\s+FROM\s+report_status_event\b", re.IGNORECASE)
    governed_paths = [*ROOT.glob("src/**/*.py"), *ROOT.glob("migrations/*.sql")]
    offenders = [
        str(path.relative_to(ROOT))
        for path in governed_paths
        if deletion.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], (
        "Report materialization owner versions depend on append-only status-event history; "
        f"deletion was introduced in: {offenders}"
    )


def _load_validator() -> ModuleType:
    script_path = ROOT / "scripts" / "validate_idea_evidence_materialization_contract.py"
    spec = importlib.util.spec_from_file_location(
        "validate_idea_evidence_materialization_contract",
        script_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
