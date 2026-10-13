"""Additive SQLite shape on report_job; PostgreSQL owns migration028."""

from __future__ import annotations

import sqlite3

CUSTODY_COLUMNS = {
    "custody_policy": "TEXT CHECK(custody_policy IS NULL OR "
    "custody_policy='retained-render-offer.v1')",
    "render_offer_body": "BLOB",
    "render_offer_sha256": "TEXT",
    "render_offer_size": "INTEGER",
    "render_offer_version": "TEXT",
    "render_offer_generation": "INTEGER NOT NULL DEFAULT 0",
    "custody_state": "TEXT CHECK(custody_state IS NULL OR custody_state IN "
    "('ready','waiting','blocked','refused','verified'))",
    "custody_epoch": "INTEGER NOT NULL DEFAULT 0",
    "custody_initial_count": "INTEGER NOT NULL DEFAULT 0 "
    "CHECK(custody_initial_count BETWEEN 0 AND 1)",
    "custody_dispatch_count": "INTEGER NOT NULL DEFAULT 0 "
    "CHECK(custody_dispatch_count BETWEEN 0 AND 3)",
    "custody_poll_count": "INTEGER NOT NULL DEFAULT 0 CHECK(custody_poll_count BETWEEN 0 AND 12)",
    "custody_window_started_at": "TEXT",
    "custody_next_attempt_at": "TEXT",
    "custody_reason": "TEXT",
    "custody_verification_json": "TEXT",
}


def ensure_custody_schema(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(report_job)")}
    for name, declaration in CUSTODY_COLUMNS.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE report_job ADD COLUMN {name} {declaration}")
    connection.execute(
        """CREATE TRIGGER IF NOT EXISTS report_custody_insert
        BEFORE INSERT ON report_job
        WHEN NEW.render_offer_body IS NOT NULL OR NEW.custody_state IS NOT NULL
          OR NEW.custody_initial_count != 0 OR NEW.custody_dispatch_count != 0
          OR NEW.custody_poll_count != 0 OR NEW.render_offer_generation != 0
        BEGIN SELECT RAISE(ABORT, 'custody_insert_shape'); END"""
    )
    connection.execute(
        """CREATE TRIGGER IF NOT EXISTS report_custody_immutable
        BEFORE UPDATE ON report_job
        WHEN NEW.custody_policy IS NOT OLD.custody_policy
          OR (OLD.render_offer_body IS NOT NULL AND (
              NEW.render_offer_body IS NOT OLD.render_offer_body
              OR NEW.render_offer_sha256 IS NOT OLD.render_offer_sha256
              OR NEW.render_offer_size IS NOT OLD.render_offer_size
              OR NEW.render_offer_version IS NOT OLD.render_offer_version
              OR NEW.render_offer_generation IS NOT OLD.render_offer_generation
              OR NEW.render_job_id IS NOT OLD.render_job_id
              OR NEW.report_request_id IS NOT OLD.report_request_id
              OR NEW.report_type IS NOT OLD.report_type
              OR NEW.render_template_id IS NOT OLD.render_template_id
              OR NEW.render_template_version IS NOT OLD.render_template_version
              OR NEW.accepted_document_contract_json IS NOT OLD.accepted_document_contract_json))
          OR (OLD.custody_policy = 'retained-render-offer.v1'
              AND OLD.archive_request_id IS NOT NULL
              AND NEW.archive_request_id IS NOT OLD.archive_request_id)
          OR (OLD.custody_state = 'verified' AND NEW.custody_state IS NOT 'verified')
          OR (OLD.custody_state = 'verified' AND (
              NEW.status IS NOT 'archived'
              OR NEW.archive_document_id IS NOT OLD.archive_document_id
              OR NEW.custody_verification_json IS NOT OLD.custody_verification_json))
        BEGIN SELECT RAISE(ABORT, 'custody_immutable'); END"""
    )
    connection.execute(
        """CREATE TRIGGER IF NOT EXISTS report_custody_shape
        BEFORE UPDATE ON report_job
        WHEN (NEW.render_offer_body IS NOT NULL AND (
            NEW.custody_policy IS NOT 'retained-render-offer.v1'
            OR NEW.render_offer_size IS NOT length(NEW.render_offer_body)
            OR NEW.render_offer_size > 8388608 OR NEW.render_offer_size < 1
            OR NEW.render_offer_sha256 IS NULL OR length(NEW.render_offer_sha256) != 64
            OR NEW.render_offer_sha256 GLOB '*[^0-9a-f]*'
            OR NEW.render_offer_version IS NOT 'json-utf8-compact.v1'
            OR NEW.render_offer_generation != 1 OR NEW.render_job_id IS NULL))
          OR (NEW.custody_policy = 'retained-render-offer.v1'
              AND NEW.custody_state IS NOT 'verified'
              AND (OLD.render_offer_sha256 IS NOT NULL OR OLD.render_job_id IS NOT NULL
                   OR OLD.custody_initial_count > 0
                   OR OLD.status IN ('rendering', 'completed', 'archiving'))
              AND NEW.status IN ('failed', 'cancelled', 'archived', 'completed',
                                 'completed_with_warnings'))
          OR NEW.custody_initial_count < OLD.custody_initial_count
          OR (NEW.custody_state = 'verified' AND (NEW.custody_verification_json IS NULL
              OR NEW.archive_request_id IS NULL OR NEW.archive_document_id IS NULL
              OR OLD.render_offer_sha256 IS NULL))
        BEGIN SELECT RAISE(ABORT, 'custody_shape'); END"""
    )
