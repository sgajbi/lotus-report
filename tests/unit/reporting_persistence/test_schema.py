from pathlib import Path

from app.reporting_persistence.schema import apply_report_schema_migrations


class _RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, query: str, params: object | None = None) -> None:
        assert params is None
        self.statements.append(query.strip())


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
