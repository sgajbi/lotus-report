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


#: A table name: bare, schema-qualified, quoted, or both.
_QUALIFIED_STATUS_EVENT = r'(?:[\w"]+\s*\.\s*)?"?report_status_event"?'
_QUALIFIED_ANY = r'(?:[\w"]+\s*\.\s*)?"?\w+"?'


def _relation_expr(qualified_name: str) -> str:
    """PostgreSQL's relation_expr around a table name.

        relation_expr: qualified_name | qualified_name '*'
                     | ONLY qualified_name | ONLY '(' qualified_name ')'

    Written from the grammar rather than from examples, because adding one
    spelling per review round is how the earlier versions of this guard kept
    naming a class while matching an instance.

    The word boundary sits in the unparenthesized branch only. After `)` a
    `\\b` would require a following word character, so a statement ending in
    `ONLY (report_status_event)` would not match. The closing paren is the
    boundary there: `ONLY (report_status_event_archive)` still cannot match,
    since `\\s*\\)` cannot follow the shorter name.
    """
    return rf"(?:ONLY\s*\(\s*{qualified_name}\s*\)|(?:ONLY\s+)?{qualified_name}\b(?:\s*\*)?)"


#: The target this guard exists to protect.
_STATUS_EVENT_TABLE = _relation_expr(_QUALIFIED_STATUS_EVENT)

#: Any other table appearing earlier in a comma-separated list. TRUNCATE takes
#: several tables at once, and the target is not always the first one.
_OTHER_TABLE = _relation_expr(_QUALIFIED_ANY)

#: Every statement that destroys history by naming the table. Not only DELETE:
#: TRUNCATE and DROP remove the same rows and would decrease
#: source_event_version identically.
HISTORY_DESTROYING_SQL = re.compile(
    r"\b(?:DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?|DROP\s+TABLE(?:\s+IF\s+EXISTS)?)\s+"
    rf"(?:{_OTHER_TABLE}\s*,\s*)*"
    rf"{_STATUS_EVENT_TABLE}",
    re.IGNORECASE,
)

_SQL_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_SQL_LINE_COMMENT = re.compile(r"--[^\n]*")


def contains_history_destroying_sql(source: str) -> bool:
    """Whether `source` destroys report_status_event history.

    Comments are removed before matching. `DELETE FROM /* retention */
    report_status_event` is valid SQL that deletes the same rows, and a pattern
    expecting the table to follow the verb directly does not see it. Replacing
    each comment with a space also absorbs arbitrary whitespace and newlines,
    so the pattern only ever meets a canonical statement.

    Two things stay out of reach, and are review's job rather than this guard's:
    SQL assembled at runtime from fragments, where no single string contains the
    statement; and destruction that never names the table, such as DROP SCHEMA
    ... CASCADE, DROP DATABASE, or restoring from a dump taken before the rows
    existed. Nested block comments are also only stripped to their first `*/`,
    which PostgreSQL would nest.
    """
    without_comments = _SQL_BLOCK_COMMENT.sub(" ", source)
    without_comments = _SQL_LINE_COMMENT.sub(" ", without_comments)
    return HISTORY_DESTROYING_SQL.search(without_comments) is not None


def test_report_status_event_history_remains_append_only() -> None:
    governed_paths = [*ROOT.glob("src/**/*.py"), *ROOT.glob("migrations/*.sql")]
    offenders = [
        str(path.relative_to(ROOT))
        for path in governed_paths
        if contains_history_destroying_sql(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], (
        "Report materialization owner versions depend on append-only status-event history; "
        f"history-destroying SQL was introduced in: {offenders}"
    )


def test_append_only_guard_catches_table_named_history_destroying_forms() -> None:
    """The guard must fail on each spelling, not only the bare DELETE.

    Without this, broadening the pattern is an unverified claim -- which is how
    the original guard came to match one form while reading as if it forbade a
    class, and how the broadened one still missed ONLY and multi-table lists.

    Scope is deliberately the table-named forms; see HISTORY_DESTROYING_SQL for
    what a table-name-anchored guard cannot reach.
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
        # PostgreSQL's ONLY, which suppresses cascade to inheriting tables and
        # deletes the named table's own rows just the same.
        "DELETE FROM ONLY report_status_event",
        "TRUNCATE TABLE ONLY report_status_event",
        "TRUNCATE ONLY public.report_status_event",
        # TRUNCATE takes a list; the target need not come first.
        "TRUNCATE report_job, report_status_event",
        "TRUNCATE TABLE report_job, ONLY public.report_status_event",
        # Comments are legal wherever whitespace is, including between the verb
        # and the table it destroys.
        "DELETE FROM /* retention */ report_status_event",
        "TRUNCATE TABLE /* q1 */ ONLY public.report_status_event",
        "DROP TABLE /* superseded */ IF EXISTS report_status_event",
        "DELETE FROM -- retention sweep\n  report_status_event",
        "TRUNCATE report_job, /* and */ report_status_event",
        # relation_expr also admits ONLY with parentheses, and a trailing * for
        # the explicit include-descendants form.
        "DELETE FROM ONLY (report_status_event)",
        "TRUNCATE TABLE ONLY (public.report_status_event)",
        'DELETE FROM ONLY ( "report_status_event" )',
        "TRUNCATE report_job, ONLY (report_status_event)",
        "DELETE FROM report_status_event *",
    ]
    for statement in must_match:
        assert contains_history_destroying_sql(statement), f"guard missed: {statement}"

    must_not_match = [
        "SELECT COUNT(*) FROM report_status_event",
        "INSERT INTO report_status_event (status_event_id) VALUES (?)",
        "DELETE FROM report_job WHERE report_job_id = ?",
        "-- report_status_event history is append-only",
        # Reads the table to choose rows in another one; destroys no history.
        "DELETE FROM audit_log WHERE id IN (SELECT id FROM report_status_event)",
        # A different table whose name merely starts the same way.
        "DELETE FROM report_status_event_archive",
        # A comment is not a statement, however destructive it reads.
        "/* DELETE FROM report_status_event would break Idea */",
        # The parenthesized form must not lose the boundary the bare form has.
        "DELETE FROM ONLY (report_status_event_archive)",
    ]
    for statement in must_not_match:
        assert not contains_history_destroying_sql(statement), (
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
