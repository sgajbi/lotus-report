"""What the transaction read proved about its own evidence.

The truncation vocabulary (`transaction_window_truncated`, the row/page
budgets) and the source-product quality notes (`trust_metadata_incomplete`,
`source_quality_not_complete`, `reconciliation_not_complete`, `page_partial`)
that every consumer of transaction rows - the table, the income summary, the
earnings statement's completeness posture - reads from one place.

Extracted from the read service verbatim: pure functions over the fetch
result, no I/O, no monetary arithmetic.
"""

from __future__ import annotations

from app.config import settings


def transaction_window_supportability(
    *,
    returned_count: int,
    source_total: int | None,
    fetched_pages: int,
    stop_reason: str | None,
    source_product: dict[str, object],
) -> dict[str, object]:
    notes: list[dict[str, object]] = []
    notes.extend(
        transaction_source_product_supportability_notes(
            source_product=source_product,
            returned_count=returned_count,
            source_total=source_total,
            fetched_pages=fetched_pages,
        )
    )
    if stop_reason is None:
        return {"status": "partial" if notes else "ready", "notes": notes}
    max_rows = settings.report_transaction_max_rows
    max_pages = settings.report_transaction_max_pages
    if stop_reason == "max_rows_reached":
        message = (
            "Transaction rows were truncated at the report-owned row budget "
            f"of {max_rows}; request a narrower window for complete transaction detail."
        )
    else:
        message = (
            "Transaction paging stopped at the report-owned page budget "
            f"of {max_pages}; request a narrower window for complete transaction detail."
        )
    notes.insert(
        0,
        {
            "code": "transaction_window_truncated",
            "severity": "warning",
            "reason": stop_reason,
            "message": message,
            "returned_count": returned_count,
            "source_total": source_total,
            "fetched_pages": fetched_pages,
            "max_rows": max_rows,
            "max_pages": max_pages,
        },
    )
    return {"status": "partial", "notes": notes}


def merge_transaction_source_product(
    *,
    current: dict[str, object],
    payload: dict[str, object],
    returned_count: int,
    source_total: int | None,
    fetched_pages: int,
) -> dict[str, object]:
    source_product = dict(current)
    for source_key, target_key in (
        ("product_name", "product_name"),
        ("product_version", "product_version"),
        ("tenant_id", "tenant_id"),
        ("generated_at", "generated_at"),
        ("as_of_date", "as_of_date"),
        ("data_quality_status", "data_quality_status"),
        ("reconciliation_status", "reconciliation_status"),
        ("latest_evidence_timestamp", "latest_evidence_timestamp"),
        ("restatement_version", "restatement_version"),
        ("source_batch_fingerprint", "source_batch_fingerprint"),
        ("snapshot_id", "snapshot_id"),
        ("content_hash", "content_hash"),
        ("policy_version", "policy_version"),
        ("correlation_id", "correlation_id"),
        ("portfolio_id", "portfolio_id"),
        ("reporting_currency", "reporting_currency"),
        ("missing_instrument_reference_count", "missing_instrument_reference_count"),
    ):
        if payload.get(source_key) is not None:
            source_product[target_key] = payload.get(source_key)
    source_product.setdefault("product_name", "TransactionLedgerWindow")
    source_product.setdefault("product_version", "v1")
    source_product["source_service"] = "lotus-core"
    source_product["source_endpoint"] = "/portfolios/{portfolio_id}/transactions"
    source_product["source_total"] = source_total
    source_product["returned_count"] = returned_count
    source_product["fetched_page_count"] = fetched_pages
    source_product["skip"] = payload.get("skip")
    source_product["limit"] = payload.get("limit")
    reason_codes = _as_list(payload.get("reason_codes"))
    if reason_codes:
        source_product["reason_codes"] = [_safe_str(reason_code) for reason_code in reason_codes]
    missing_security_ids = _as_list(payload.get("missing_instrument_security_ids"))
    if missing_security_ids:
        source_product["missing_instrument_security_ids"] = [
            _safe_str(security_id) for security_id in missing_security_ids
        ]
    return source_product


def transaction_source_product_supportability_notes(
    *,
    source_product: dict[str, object],
    returned_count: int,
    source_total: int | None,
    fetched_pages: int,
) -> list[dict[str, object]]:
    notes: list[dict[str, object]] = []
    required_fields = (
        "product_name",
        "product_version",
        "tenant_id",
        "generated_at",
        "as_of_date",
        "data_quality_status",
        "reconciliation_status",
        "latest_evidence_timestamp",
        "restatement_version",
        "source_batch_fingerprint",
        "snapshot_id",
        "policy_version",
        "correlation_id",
    )
    missing_fields = [
        field for field in required_fields if source_product.get(field) in (None, "", [], {})
    ]
    if missing_fields:
        notes.append(
            {
                "code": "transaction_window_trust_metadata_incomplete",
                "severity": "warning",
                "missing_fields": missing_fields,
                "message": (
                    "TransactionLedgerWindow source-product metadata is incomplete; "
                    "transaction supportability is partial until core trust metadata is "
                    "available."
                ),
                "returned_count": returned_count,
                "source_total": source_total,
                "fetched_pages": fetched_pages,
            }
        )
    data_quality_status = _safe_str(source_product.get("data_quality_status")).upper()
    if data_quality_status and data_quality_status != "COMPLETE":
        notes.append(
            {
                "code": "transaction_window_source_quality_not_complete",
                "severity": "warning",
                "data_quality_status": data_quality_status,
                "reason_codes": source_product.get("reason_codes", []),
                "message": (
                    "lotus-core marked the transaction ledger window as not complete; "
                    "report transaction coverage must remain partial."
                ),
            }
        )
    reconciliation_status = _safe_str(source_product.get("reconciliation_status")).upper()
    if reconciliation_status and reconciliation_status not in {"RECONCILED", "COMPLETE"}:
        notes.append(
            {
                "code": "transaction_window_reconciliation_not_complete",
                "severity": "warning",
                "reconciliation_status": reconciliation_status,
                "message": (
                    "lotus-core transaction ledger reconciliation is not complete for this window."
                ),
            }
        )
    if source_total is not None and returned_count < source_total:
        notes.append(
            {
                "code": "transaction_window_page_partial",
                "severity": "warning",
                "returned_count": returned_count,
                "source_total": source_total,
                "fetched_pages": fetched_pages,
                "message": (
                    "The report payload contains fewer transaction rows than the source "
                    "ledger window."
                ),
            }
        )
    return notes


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return []


def _safe_str(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""
