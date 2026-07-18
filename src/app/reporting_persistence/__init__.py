"""Shared PostgreSQL schema lifecycle for durable reporting stores."""

from app.reporting_persistence.ownership import ManagedPostgresAdapter
from app.reporting_persistence.schema import (
    ReportSchemaCompatibilityError,
    ReportSchemaError,
    ReportSchemaMigrationError,
    apply_report_schema_migrations,
)

__all__ = [
    "ManagedPostgresAdapter",
    "ReportSchemaCompatibilityError",
    "ReportSchemaError",
    "ReportSchemaMigrationError",
    "apply_report_schema_migrations",
]
