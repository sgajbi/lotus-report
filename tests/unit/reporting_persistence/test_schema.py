from pathlib import Path

import pytest
from psycopg.errors import UndefinedColumn

from app.reporting_persistence.schema import (
    CURRENT_SCHEMA_VERSION,
    LEGACY_STATUS_EVENT_BASELINE,
    LEGACY_STATUS_EVENT_COLUMNS,
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
    def __init__(
        self,
        *,
        table_exists: bool = False,
        columns: dict[str, str] | None = None,
        failure_fragment: str | None = None,
    ) -> None:
        self.statements: list[str] = []
        self._table_exists = table_exists
        self._columns = columns or {}
        self._failure_fragment = failure_fragment

    def execute(self, query: str, params: object | None = None) -> _Result:
        assert params is None
        if "information_schema.tables" in query:
            return _Result(one={"table_exists": self._table_exists})
        if "information_schema.columns" in query:
            return _Result(
                many=[
                    {"column_name": name, "data_type": data_type}
                    for name, data_type in self._columns.items()
                ]
            )
        if self._failure_fragment and self._failure_fragment in query:
            raise UndefinedColumn("migration statement failed")
        self.statements.append(query.strip())
        return _Result()


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
    columns = {column: "text" for column in LEGACY_STATUS_EVENT_COLUMNS}
    columns.update(
        {
            "event_schema_version": "text",
            "event_family": "text",
            "event_payload_json": "jsonb",
            "event_idempotency_key": "text",
        }
    )
    connection = _RecordingConnection(table_exists=True, columns=columns)

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
        match="incompatible=event_family:integer",
    ):
        validate_supported_report_schema(connection)


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
