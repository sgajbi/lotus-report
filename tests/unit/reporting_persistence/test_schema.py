from pathlib import Path

import pytest
from psycopg.errors import UndefinedColumn

from app.reporting_persistence.schema import (
    CURRENT_SCHEMA_VERSION,
    LEGACY_STATUS_EVENT_BASELINE,
    LEGACY_STATUS_EVENT_COLUMNS,
    STATUS_EVENT_CONTRACT_NULLABILITY,
    ReportSchemaCompatibilityError,
    ReportSchemaMigrationError,
    apply_report_schema_migrations,
    validate_supported_report_schema,
)


class _Result:
    def __init__(self, *, one: object | None = None, many: list[object] | None = None) -> None:
        self._one = one
        self._many = many or []

    def fetchone(self) -> object | None:
        return self._one

    def fetchall(self) -> list[object]:
        return self._many


class _RecordingConnection:
    """A `MigrationConnection` double that models the applied-migration ledger.

    The ledger is real state rather than canned responses: an INSERT the runner
    issues is visible to its next SELECT, so replay behavior is testable here
    the same way it behaves against PostgreSQL.
    """

    def __init__(
        self,
        *,
        table_exists: bool = False,
        columns: dict[str, str] | None = None,
        nullability: dict[str, str] | None = None,
        failure_fragment: str | None = None,
        recorded: list[str] | None = None,
    ) -> None:
        self.statements: list[str] = []
        self.recorded: list[str] = list(recorded or [])
        self._table_exists = table_exists
        self._columns = columns or {}
        self._nullability = nullability or {}
        self._failure_fragment = failure_fragment

    def execute(self, query: str, params: object | None = None) -> _Result:
        assert params is None
        text = query.strip()
        if "pg_advisory_xact_lock" in text:
            return _Result()
        if "information_schema.tables" in text:
            return _Result(one={"table_exists": self._table_exists})
        if "information_schema.columns" in text:
            return _Result(
                many=[
                    {
                        "column_name": name,
                        "data_type": data_type,
                        "is_nullable": self._nullability.get(
                            name,
                            STATUS_EVENT_CONTRACT_NULLABILITY.get(name, "YES"),
                        ),
                    }
                    for name, data_type in self._columns.items()
                ]
            )
        if "CREATE TABLE IF NOT EXISTS report_schema_migration" in text:
            return _Result()
        if text.startswith("SELECT migration_name FROM report_schema_migration"):
            return _Result(many=[{"migration_name": name} for name in self.recorded])
        if text.startswith("INSERT INTO report_schema_migration"):
            name = text.split("VALUES ('", 1)[1].rstrip("')").replace("''", "'")
            self.recorded.append(name)
            return _Result()
        if self._failure_fragment and self._failure_fragment in text:
            raise UndefinedColumn("migration statement failed")
        self.statements.append(text)
        return _Result()


def _current_contract_columns() -> dict[str, str]:
    columns = {column: "text" for column in LEGACY_STATUS_EVENT_COLUMNS}
    columns.update(
        {
            "event_schema_version": "text",
            "event_family": "text",
            "event_payload_json": "jsonb",
            "event_idempotency_key": "text",
        }
    )
    return columns


def _current_contract_nullability() -> dict[str, str]:
    return {
        "event_schema_version": "NO",
        "event_family": "NO",
        "event_payload_json": "NO",
        "event_idempotency_key": "YES",
    }


def test_apply_report_schema_migrations_uses_filename_order(tmp_path: Path) -> None:
    (tmp_path / "010_second.sql").write_text(
        "CREATE TABLE second_table(id TEXT);", encoding="utf-8"
    )
    (tmp_path / "001_first.sql").write_text("CREATE TABLE first_table(id TEXT);", encoding="utf-8")
    connection = _RecordingConnection()

    applied = apply_report_schema_migrations(connection, migrations_dir=tmp_path)

    assert applied == ("001_first.sql", "010_second.sql")
    assert connection.statements == [
        "CREATE TABLE first_table(id TEXT)",
        "CREATE TABLE second_table(id TEXT)",
    ]
    assert connection.recorded == ["001_first.sql", "010_second.sql"]


def test_apply_report_schema_migrations_applies_each_file_once(tmp_path: Path) -> None:
    """A repeat run consults the ledger and executes no migration DDL (report#376)."""
    (tmp_path / "001_first.sql").write_text("CREATE TABLE first_table(id TEXT);", encoding="utf-8")
    connection = _RecordingConnection()

    first = apply_report_schema_migrations(connection, migrations_dir=tmp_path)
    second = apply_report_schema_migrations(connection, migrations_dir=tmp_path)

    assert first == ("001_first.sql",)
    assert second == ()
    assert connection.statements == ["CREATE TABLE first_table(id TEXT)"]


def test_apply_report_schema_migrations_refuses_out_of_order_pending(tmp_path: Path) -> None:
    """A pending file sorting before a recorded one is edited history, not work.

    Its DDL was written against a schema shape that no longer exists, so the
    run must refuse before mutation rather than apply it late.
    """
    for name in ("001_first.sql", "002_between.sql", "003_third.sql"):
        (tmp_path / name).write_text(f"CREATE TABLE t_{name[:3]}(id TEXT);", encoding="utf-8")
    connection = _RecordingConnection(recorded=["001_first.sql", "003_third.sql"])

    with pytest.raises(
        ReportSchemaMigrationError,
        match=(
            "report_schema_migration_out_of_order:pending=002_between.sql:"
            "applied_through=003_third.sql:target=report-ledger-v1"
        ),
    ):
        apply_report_schema_migrations(connection, migrations_dir=tmp_path)

    assert connection.statements == []
    assert connection.recorded == ["001_first.sql", "003_third.sql"]


def test_recorded_migrations_unknown_to_this_binary_are_tolerated(tmp_path: Path) -> None:
    """An older binary starting against a newer schema applies its own gap only.

    Forward-only additive migrations are exactly the promise that this is safe;
    refusing here would turn every rolling deployment restart into an outage.
    """
    (tmp_path / "001_first.sql").write_text("CREATE TABLE first_table(id TEXT);", encoding="utf-8")
    (tmp_path / "002_second.sql").write_text(
        "CREATE TABLE second_table(id TEXT);", encoding="utf-8"
    )
    connection = _RecordingConnection(recorded=["001_first.sql", "999_future.sql"])

    applied = apply_report_schema_migrations(connection, migrations_dir=tmp_path)

    assert applied == ("002_second.sql",)
    assert connection.statements == ["CREATE TABLE second_table(id TEXT)"]


def test_apply_report_schema_migrations_skips_empty_statements(tmp_path: Path) -> None:
    (tmp_path / "001_schema.sql").write_text(
        ";\nCREATE TABLE report_job(id TEXT);\n;\n", encoding="utf-8"
    )
    connection = _RecordingConnection()

    apply_report_schema_migrations(connection, migrations_dir=tmp_path)

    assert connection.statements == ["CREATE TABLE report_job(id TEXT)"]


def test_validate_supported_report_schema_accepts_legacy_baseline() -> None:
    connection = _RecordingConnection(
        table_exists=True,
        columns={column: "text" for column in LEGACY_STATUS_EVENT_COLUMNS},
    )

    detected = validate_supported_report_schema(connection)

    assert detected == LEGACY_STATUS_EVENT_BASELINE


def test_validate_supported_report_schema_accepts_current_contract() -> None:
    connection = _RecordingConnection(
        table_exists=True,
        columns=_current_contract_columns(),
        nullability=_current_contract_nullability(),
    )

    detected = validate_supported_report_schema(connection)

    assert detected == CURRENT_SCHEMA_VERSION


def test_validate_supported_report_schema_rejects_unrecognized_legacy_shape() -> None:
    columns = {column: "text" for column in LEGACY_STATUS_EVENT_COLUMNS - {"actor"}}
    connection = _RecordingConnection(table_exists=True, columns=columns)

    with pytest.raises(
        ReportSchemaCompatibilityError,
        match=(
            "report_schema_upgrade_unsupported:detected=unrecognized:"
            "target=report-ledger-v1:table=report_status_event:missing=actor"
        ),
    ):
        validate_supported_report_schema(connection)


def test_validate_supported_report_schema_rejects_incompatible_contract_type() -> None:
    columns = {column: "text" for column in LEGACY_STATUS_EVENT_COLUMNS}
    columns["event_family"] = "integer"
    connection = _RecordingConnection(table_exists=True, columns=columns)

    with pytest.raises(
        ReportSchemaCompatibilityError,
        match="incompatible=event_family:type=integer:expected_type=text",
    ):
        validate_supported_report_schema(connection)


@pytest.mark.parametrize(
    ("column", "actual_nullable", "expected_nullable"),
    [
        ("event_schema_version", "YES", "NO"),
        ("event_family", "YES", "NO"),
        ("event_payload_json", "YES", "NO"),
        ("event_idempotency_key", "NO", "YES"),
    ],
)
def test_validate_supported_report_schema_rejects_incompatible_contract_nullability(
    column: str,
    actual_nullable: str,
    expected_nullable: str,
) -> None:
    nullability = _current_contract_nullability()
    nullability[column] = actual_nullable
    connection = _RecordingConnection(
        table_exists=True,
        columns=_current_contract_columns(),
        nullability=nullability,
    )

    with pytest.raises(
        ReportSchemaCompatibilityError,
        match=(
            f"incompatible={column}:nullable={actual_nullable}:"
            f"expected_nullable={expected_nullable}"
        ),
    ):
        validate_supported_report_schema(connection)


def test_apply_report_schema_migrations_rejects_nullability_before_mutation(
    tmp_path: Path,
) -> None:
    (tmp_path / "001_should_not_run.sql").write_text(
        "CREATE TABLE mutation_marker(id TEXT);",
        encoding="utf-8",
    )
    nullability = _current_contract_nullability()
    nullability["event_payload_json"] = "YES"
    connection = _RecordingConnection(
        table_exists=True,
        columns=_current_contract_columns(),
        nullability=nullability,
    )

    with pytest.raises(
        ReportSchemaCompatibilityError,
        match="event_payload_json:nullable=YES:expected_nullable=NO",
    ):
        apply_report_schema_migrations(connection, migrations_dir=tmp_path)

    assert connection.statements == []
    assert connection.recorded == []


def test_apply_report_schema_migrations_classifies_postgres_failure(tmp_path: Path) -> None:
    (tmp_path / "001_schema.sql").write_text(
        "CREATE INDEX broken_index ON report_status_event(event_family);",
        encoding="utf-8",
    )
    connection = _RecordingConnection(failure_fragment="broken_index")

    with pytest.raises(
        ReportSchemaMigrationError,
        match=(
            "report_schema_migration_failed:migration=001_schema.sql:"
            "sqlstate=42703:target=report-ledger-v1"
        ),
    ):
        apply_report_schema_migrations(connection, migrations_dir=tmp_path)

    assert connection.recorded == [], "a failed migration must not be recorded as applied"
