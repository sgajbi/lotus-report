"""Shared PostgreSQL schema lifecycle for durable reporting stores."""

from app.reporting_persistence.schema import apply_report_schema_migrations

__all__ = ["apply_report_schema_migrations"]
