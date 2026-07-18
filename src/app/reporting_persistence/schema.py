from __future__ import annotations

from pathlib import Path
from typing import Protocol


class MigrationConnection(Protocol):
    def execute(self, query: str, params: object | None = None) -> object: ...


MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def apply_report_schema_migrations(
    connection: MigrationConnection,
    *,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> tuple[str, ...]:
    """Apply the ordered, forward-only Report schema using the production path."""

    applied: list[str] = []
    for migration_path in sorted(migrations_dir.glob("*.sql")):
        schema = migration_path.read_text(encoding="utf-8")
        for statement in schema.split(";"):
            if statement.strip():
                connection.execute(statement)
        applied.append(migration_path.name)
    return tuple(applied)
