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

import re
from datetime import date, datetime
from typing import cast

from app.config import settings

TRUST_METADATA_FIELDS = (
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
# Required by Core's PaginatedTransactionResponse in addition to trust metadata.
REQUIRED_PAGE_SCOPE_FIELDS = ("portfolio_id",)
# Core's reconstruction scope is the full, unpaginated window. Generation time,
# operational correlation and page content hashes do not identify that scope.
WINDOW_IDENTITY_FIELDS = (
    "product_name",
    "product_version",
    "tenant_id",
    "portfolio_id",
    "reporting_currency",
    "as_of_date",
    "latest_evidence_timestamp",
    "restatement_version",
    "source_batch_fingerprint",
    "snapshot_id",
    "policy_version",
)
MAX_SOURCE_REASON_CODES = 64
SOURCE_REASON_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def _page_reason_codes(payload: dict[str, object]) -> tuple[list[str], bool]:
    raw = payload.get("reason_codes", [])
    values = _as_list(raw)
    valid = {
        code for code in values if isinstance(code, str) and SOURCE_REASON_CODE.fullmatch(code)
    }
    bounded = (
        not isinstance(raw, list)
        or any(
            not isinstance(code, str) or not SOURCE_REASON_CODE.fullmatch(code) for code in values
        )
        or len(valid) > MAX_SOURCE_REASON_CODES
    )
    return sorted(valid)[:MAX_SOURCE_REASON_CODES], bounded


def _missing_trust_fields(payload: dict[str, object]) -> list[str]:
    temporal = {"generated_at", "as_of_date", "latest_evidence_timestamp"}
    return [
        field
        for field in (*TRUST_METADATA_FIELDS, *REQUIRED_PAGE_SCOPE_FIELDS)
        if not (
            (isinstance(payload.get(field), str) and bool(str(payload[field]).strip()))
            or (field in temporal and isinstance(payload.get(field), (date, datetime)))
        )
    ]


def _page_records(source_product: dict[str, object]) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], page)
        for page in _as_list(source_product.get("page_evidence"))
        if isinstance(page, dict)
    ]


def _aggregate_page_status(pages: list[dict[str, object]], field: str) -> str:
    states = {_safe_str(page.get(field)).strip().upper() for page in pages}
    if "" in states or "UNKNOWN" in states:
        return "UNKNOWN"
    if field == "data_quality_status":
        return "COMPLETE" if states == {"COMPLETE"} else "PARTIAL"
    unhealthy = states - {"RECONCILED", "COMPLETE"}
    if not unhealthy:
        return "RECONCILED"
    if len(unhealthy) == 1:
        return next(iter(unhealthy))
    return "UNKNOWN"


def _page_status(value: object) -> str:
    status = _safe_str(value).strip().upper()
    return status if SOURCE_REASON_CODE.fullmatch(status) else "UNKNOWN"


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
    missing_fields = _missing_trust_fields(payload)
    pages = _page_records(current)
    conflicts = sorted(
        field
        for field in WINDOW_IDENTITY_FIELDS
        if field in current
        and field not in missing_fields
        and (payload.get(field) is not None or field == "reporting_currency")
        and current[field] != payload.get(field)
    )
    page_reasons, reasons_bounded = _page_reason_codes(payload)
    pages.append(
        {
            "page": fetched_pages,
            "missing_fields": missing_fields,
            "identity_conflicts": conflicts,
            "reason_codes": page_reasons,
            "reasons_bounded": reasons_bounded,
            "data_quality_status": _page_status(payload.get("data_quality_status")),
            "reconciliation_status": _page_status(payload.get("reconciliation_status")),
        }
    )
    for source_key, target_key in (
        ("product_name", "product_name"),
        ("product_version", "product_version"),
        ("tenant_id", "tenant_id"),
        ("generated_at", "generated_at"),
        ("as_of_date", "as_of_date"),
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
        if payload.get(source_key) is not None and source_key not in missing_fields:
            if source_key in WINDOW_IDENTITY_FIELDS and target_key in source_product:
                continue
            source_product[target_key] = payload.get(source_key)
    if "reporting_currency" not in missing_fields:
        source_product.setdefault("reporting_currency", payload.get("reporting_currency"))
    source_product.setdefault("product_name", "TransactionLedgerWindow")
    source_product.setdefault("product_version", "v1")
    source_product["source_service"] = "lotus-core"
    source_product["source_endpoint"] = "/portfolios/{portfolio_id}/transactions"
    source_product["source_total"] = source_total
    source_product["returned_count"] = returned_count
    source_product["fetched_page_count"] = fetched_pages
    source_product["skip"] = payload.get("skip")
    source_product["limit"] = payload.get("limit")
    source_product["page_evidence"] = pages
    source_product["page_evidence_policy"] = "transaction-page-evidence.v1"
    source_product["data_quality_status"] = _aggregate_page_status(pages, "data_quality_status")
    source_product["reconciliation_status"] = _aggregate_page_status(pages, "reconciliation_status")
    reasons = sorted(
        {_safe_str(code) for page in pages for code in _as_list(page.get("reason_codes"))}
    )
    source_product["reason_codes"] = reasons[:MAX_SOURCE_REASON_CODES]
    source_product["reason_evidence_bounded"] = len(reasons) > MAX_SOURCE_REASON_CODES or any(
        page.get("reasons_bounded") for page in pages
    )
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
    pages = _page_records(source_product)
    missing_fields = (
        sorted({str(field) for page in pages for field in _as_list(page.get("missing_fields"))})
        if pages
        else _missing_trust_fields(source_product)
    )
    if missing_fields:
        notes.append(
            {
                "code": "transaction_window_trust_metadata_incomplete",
                "severity": "warning",
                "missing_fields": missing_fields,
                "pages": [page["page"] for page in pages if page.get("missing_fields")],
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
    conflicts = sorted(
        {str(field) for page in pages for field in _as_list(page.get("identity_conflicts"))}
    )
    if conflicts:
        notes.append(
            {
                "code": "transaction_window_source_identity_incoherent",
                "severity": "warning",
                "fields": conflicts,
                "pages": [page["page"] for page in pages if page.get("identity_conflicts")],
                "message": (
                    "Consumed transaction pages do not share one source scope and revision; "
                    "combined evidence is partial."
                ),
            }
        )
    if source_product.get("reason_evidence_bounded"):
        notes.append(
            {
                "code": "transaction_window_reason_evidence_bounded",
                "severity": "warning",
                "message": (
                    "Source reason evidence exceeded the bounded machine-code contract; "
                    "full supportability is not established."
                ),
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
