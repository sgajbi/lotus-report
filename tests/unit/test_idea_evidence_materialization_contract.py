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


#: The table name as SQL may legally spell it: bare, schema-qualified, quoted,
#: or both. Matching only the bare form lets a qualified deletion through a
#: guard whose whole purpose is to forbid deletion.
_STATUS_EVENT_TABLE = r'(?:[\w"]+\s*\.\s*)?"?report_status_event"?'

#: Every statement that destroys history, not only DELETE. TRUNCATE and DROP
#: remove the same rows and would decrease source_event_version identically.
HISTORY_DESTROYING_SQL = re.compile(
    r"\b(?:DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?|DROP\s+TABLE(?:\s+IF\s+EXISTS)?)"
    rf"\s+{_STATUS_EVENT_TABLE}",
    re.IGNORECASE,
)


def test_report_status_event_history_remains_append_only() -> None:
    governed_paths = [*ROOT.glob("src/**/*.py"), *ROOT.glob("migrations/*.sql")]
    offenders = [
        str(path.relative_to(ROOT))
        for path in governed_paths
        if HISTORY_DESTROYING_SQL.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], (
        "Report materialization owner versions depend on append-only status-event history; "
        f"history-destroying SQL was introduced in: {offenders}"
    )


def test_append_only_guard_catches_every_history_destroying_form() -> None:
    """The guard must fail on each spelling, not only the bare DELETE.

    Without this, broadening the pattern is an unverified claim -- which is how
    the original guard came to match one form while reading as if it forbade a
    class.
    """
    must_match = [
        "DELETE FROM report_status_event",
        "DELETE FROM public.report_status_event",
        'DELETE FROM "report_status_event"',
        'DELETE FROM public."report_status_event"',
        "delete from REPORT_STATUS_EVENT",
        "TRUNCATE report_status_event",
        "TRUNCATE TABLE public.report_status_event",
        "DROP TABLE report_status_event",
        "DROP TABLE IF EXISTS public.report_status_event",
    ]
    for statement in must_match:
        assert HISTORY_DESTROYING_SQL.search(statement), f"guard missed: {statement}"

    must_not_match = [
        "SELECT COUNT(*) FROM report_status_event",
        "INSERT INTO report_status_event (status_event_id) VALUES (?)",
        "DELETE FROM report_job WHERE report_job_id = ?",
        "-- report_status_event history is append-only",
    ]
    for statement in must_not_match:
        assert not HISTORY_DESTROYING_SQL.search(statement), (
            f"guard false-positived on: {statement}"
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
