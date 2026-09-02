"""What the portfolio paid you, and what you locked in (issue #249).

The document answered "what did the portfolio pay me this period" and "what
did I actually realize" only through the transaction table: the reader had to
scan it and do the arithmetic themselves. This block is the statement that
arithmetic would have produced, composed from evidence the capture already
established, and it renders beside the table it summarises - the statement is
the answer to "what does this table add up to", so it sits where a reader
wanting the total is already looking.

Two properties are load-bearing:

- **A floor is not a total.** The transaction read is capped, so sums over a
  truncated window understate the period. `completeness` states which claim
  the money amounts make: `complete` means period totals; `window_truncated`
  means "at least this much, based on the transactions reviewed", and the
  reviewed/source counts travel with it so the page can say so. Presenting a
  floor as a total would be a false monetary statement on an archived
  document.
- **This is portfolio earnings, not a tax document.** The methodology travels
  with the money: the realized basis is transaction-level, and tax-lot
  jurisdiction treatment is not sourced. The page must not imply
  tax-reportable numbers.

Sized to the agreed Render budget: the bottom ~40% of the transaction page as
two compact side-by-side blocks (income; realized P&L). Anything that would
grow past that is renegotiated with Render first, not appended.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

POSTURE_READY = "ready"
POSTURE_EMPTY = "empty"
POSTURE_UNAVAILABLE = "unavailable"

COMPLETENESS_COMPLETE = "complete"
COMPLETENESS_WINDOW_TRUNCATED = "window_truncated"

_TRUNCATION_CODE = "transaction_window_truncated"

#: Income membership is lotus-core's INCOME_RECOGNITION_TRANSACTION_TYPES
#: verbatim (coupons book as INTEREST). Presented in this order.
_INCOME_TYPES = ("DIVIDEND", "INTEREST")


def build_earnings_statement(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The resolved statement, or a posture explaining why there is none."""

    section = _as_dict(snapshot.get("incomeAndActivity"))
    if not section:
        # The transactions section was not ordered; nothing is promised.
        return {}

    income = _as_dict(section.get("incomeSummary"))
    realized = _as_dict(section.get("realizedPnlSummary"))
    if not income or not realized:
        # Ordered, and the evidence is not there to compose: a fact about the
        # DATA, said rather than summed into a statement that looks whole.
        return {"posture": POSTURE_UNAVAILABLE, "notes": _notes(section)}

    completeness, reviewed_count, source_count = _completeness(section)

    income_count = _count(income.get("transaction_count"))
    realized_present = _text(realized.get("status")) == "present"
    posture = POSTURE_READY
    if income_count == 0 and not realized_present:
        # The portfolio received no income and realized nothing this period.
        # A true statement about the portfolio, drawn as one line - unless the
        # window was truncated, in which case "nothing happened" was never
        # established over the whole period and the honest posture is a
        # truncated statement of zeros, not an empty claim.
        posture = POSTURE_EMPTY if completeness == COMPLETENESS_COMPLETE else POSTURE_READY

    return {
        "posture": posture,
        "completeness": completeness,
        "reviewed_transaction_count": reviewed_count,
        "source_transaction_count": source_count,
        "income": _income_block(income),
        "realized_pnl": _realized_block(realized, snapshot),
        "methodology": _methodology(realized),
        "notes": _notes(section),
    }


def _income_block(income: dict[str, Any]) -> dict[str, Any]:
    by_type = _as_dict(income.get("by_income_type"))
    block: dict[str, Any] = {
        "transaction_count": _count(income.get("transaction_count")),
        "gross": _money_text(income.get("gross_amount_reporting_currency")),
        "withholding_tax": _money_text(income.get("withholding_tax_reporting_currency")),
        "other_deductions": _money_text(income.get("other_deductions_reporting_currency")),
        "net": _money_text(income.get("net_amount_reporting_currency")),
    }
    if by_type:
        # Present only when the capture carries the split: a snapshot taken
        # before the split was forwarded gets a statement without it rather
        # than one reconstructed from data the package does not hold.
        block["by_type"] = [
            {
                "income_type": income_type,
                "net": _money_text(
                    _as_dict(by_type.get(income_type)).get("net_amount_reporting_currency")
                ),
                "transaction_count": _count(
                    _as_dict(by_type.get(income_type)).get("transaction_count")
                ),
            }
            for income_type in _INCOME_TYPES
            if income_type in by_type
        ]
    return block


def _realized_block(realized: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": _text(realized.get("status")) or "not_applicable",
        "transaction_count": _count(realized.get("transaction_count")),
        "net": _money_text(realized.get("total_realized_pnl_reporting_currency")),
        "gains": _money_text(realized.get("total_realized_gains_reporting_currency")),
        "losses": _money_text(realized.get("total_realized_losses_reporting_currency")),
        "largest_gain": _key_figure(realized.get("largest_realized_gain"), snapshot),
        "largest_loss": _key_figure(realized.get("largest_realized_loss"), snapshot),
    }


def _key_figure(value: Any, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """The single trade an advisor can talk about, named for a reader.

    The capture's key figure carries only identifiers; the readable name lives
    in holdings, joined here exactly as contribution ranking joins its rows so
    Render never joins anything.
    """

    figure = _as_dict(value)
    if not figure:
        return None
    security_id = _text(figure.get("security_id"))
    names = _security_names(snapshot)
    return {
        "security_name": names.get(security_id) or security_id or "Not available",
        "amount": _money_text(figure.get("realized_pnl_reporting_currency")),
        "transaction_date": _text(figure.get("transaction_date")) or None,
    }


def _methodology(realized: dict[str, Any]) -> dict[str, Any]:
    source = _as_dict(realized.get("methodology"))
    return {
        "basis": _text(source.get("basis")) or None,
        "tax_lot_jurisdiction_treatment": (
            _text(source.get("tax_lot_jurisdiction_treatment")) or None
        ),
    }


def _completeness(section: dict[str, Any]) -> tuple[str, int | None, int | None]:
    """Which claim the sums make, from the capture's own truncation evidence."""

    for note in _raw_notes(section):
        if _text(note.get("code")) == _TRUNCATION_CODE:
            return (
                COMPLETENESS_WINDOW_TRUNCATED,
                _count_or_none(note.get("returned_count")),
                _count_or_none(note.get("source_total")),
            )
    return COMPLETENESS_COMPLETE, None, None


def _notes(section: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "code": _text(note.get("code")) or None,
            "severity": _text(note.get("severity")) or None,
            "message": _text(note.get("message")) or None,
        }
        for note in _raw_notes(section)
    ]


def _raw_notes(section: dict[str, Any]) -> list[dict[str, Any]]:
    supportability = _as_dict(section.get("supportability"))
    return [note for note in supportability.get("notes") or [] if isinstance(note, dict)]


def _security_names(snapshot: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    holdings = _as_dict(snapshot.get("holdings"))
    for bucket in _as_dict(holdings.get("holdingsByAssetClass")).values():
        if not isinstance(bucket, list):
            continue
        for row in bucket:
            if not isinstance(row, dict):
                continue
            security_id = _text(row.get("security_id"))
            name = _text(row.get("security_name")) or _text(row.get("instrument_name"))
            if security_id and name:
                names[security_id] = name
    return names


def _money_text(value: Any) -> str:
    decimal_value = _decimal(value)
    if decimal_value is None:
        return "Not available"
    return f"{decimal_value.quantize(Decimal('0.01'))}"


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return int(value)


def _count_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""
