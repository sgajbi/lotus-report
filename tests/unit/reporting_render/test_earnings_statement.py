"""The transaction table's totals are stated, not left to the reader (#249)."""

from app.reporting_render.earnings_statement import build_earnings_statement


def _income_summary(count=9, gross=18420.0, withholding=2210.4, net=16209.6, by_type=None):
    summary = {
        "transaction_count": count,
        "gross_amount_reporting_currency": gross,
        "withholding_tax_reporting_currency": withholding,
        "other_deductions_reporting_currency": 0.0,
        "net_amount_reporting_currency": net,
    }
    if by_type is not None:
        summary["by_income_type"] = by_type
    return summary


def _realized_summary(status="present", **overrides):
    summary = {
        "status": status,
        "transaction_count": 2,
        "total_realized_pnl_reporting_currency": 1250.0,
        "total_realized_gains_reporting_currency": 2000.0,
        "total_realized_losses_reporting_currency": -750.0,
        "largest_realized_gain": {
            "security_id": "SEC1",
            "transaction_date": "2026-03-14",
            "realized_pnl_reporting_currency": 2000.0,
        },
        "largest_realized_loss": {
            "security_id": "SEC2",
            "transaction_date": "2026-05-02",
            "realized_pnl_reporting_currency": -750.0,
        },
        "methodology": {
            "basis": "transaction_level_realized_gain_loss",
            "tax_lot_jurisdiction_treatment": "not_sourced",
        },
    }
    summary.update(overrides)
    return summary


def _snapshot(income=None, realized=None, notes=None, holdings=True):
    snapshot = {
        "incomeAndActivity": {
            "incomeSummary": income if income is not None else _income_summary(),
            "realizedPnlSummary": realized if realized is not None else _realized_summary(),
            "supportability": {"status": "partial" if notes else "ready", "notes": notes or []},
        }
    }
    if holdings:
        snapshot["holdings"] = {
            "holdingsByAssetClass": {
                "Equity": [{"security_id": "SEC1", "security_name": "Acme Corp"}]
            }
        }
    return snapshot


def _truncation_note(returned=5000, source=9000):
    return {
        "code": "transaction_window_truncated",
        "severity": "warning",
        "message": "Transaction rows were truncated at the report-owned row budget.",
        "returned_count": returned,
        "source_total": source,
    }


def test_the_statement_answers_what_the_table_adds_up_to():
    """Income as gross -> withholding -> net, realized P&L with both sides -
    the arithmetic the reader previously had to do across transaction rows."""

    statement = build_earnings_statement(_snapshot())

    assert statement["posture"] == "ready"
    assert statement["completeness"] == "complete"
    assert statement["income"]["gross"] == "18420.00"
    assert statement["income"]["withholding_tax"] == "2210.40"
    assert statement["income"]["net"] == "16209.60"
    assert statement["realized_pnl"]["net"] == "1250.00"
    assert statement["realized_pnl"]["gains"] == "2000.00"
    assert statement["realized_pnl"]["losses"] == "-750.00"


def test_the_largest_trades_are_named_for_a_reader():
    """ "You realized 2,000 selling Acme in March" is an advisor sentence; a
    total alone is not. The name is joined from holdings so Render never
    joins anything, and an id the holdings cannot name falls back to the id
    rather than vanishing."""

    statement = build_earnings_statement(_snapshot())

    gain = statement["realized_pnl"]["largest_gain"]
    loss = statement["realized_pnl"]["largest_loss"]
    assert gain == {
        "security_name": "Acme Corp",
        "amount": "2000.00",
        "transaction_date": "2026-03-14",
    }
    assert loss["security_name"] == "SEC2"


def test_a_truncated_window_makes_the_sums_a_floor_not_a_total():
    """The load-bearing property. Sums over a capped read understate the
    period, and presenting a floor as a total would be a false monetary
    statement on an archived document. The reviewed/source counts travel with
    the posture so the page can say "at least X, based on the N transactions
    reviewed"."""

    statement = build_earnings_statement(_snapshot(notes=[_truncation_note()]))

    assert statement["completeness"] == "window_truncated"
    assert statement["reviewed_transaction_count"] == 5000
    assert statement["source_transaction_count"] == 9000
    # The amounts themselves are unchanged - the floor is the honest sum, and
    # the posture is what changes its meaning.
    assert statement["income"]["net"] == "16209.60"


def test_a_quiet_period_is_a_finding_and_a_truncated_quiet_window_is_not():
    """Zero income and nothing realized over a COMPLETE window is a true
    statement about the portfolio, drawn as an empty statement. The same zeros
    over a TRUNCATED window were never established - "nothing happened" cannot
    be claimed from a partial read, so the posture stays ready with truncated
    zeros rather than an empty claim."""

    quiet_income = _income_summary(count=0, gross=0.0, withholding=0.0, net=0.0)
    quiet_realized = _realized_summary(
        status="not_applicable",
        transaction_count=0,
        total_realized_pnl_reporting_currency=0.0,
        total_realized_gains_reporting_currency=0.0,
        total_realized_losses_reporting_currency=0.0,
        largest_realized_gain=None,
        largest_realized_loss=None,
    )

    complete = build_earnings_statement(_snapshot(income=quiet_income, realized=quiet_realized))
    truncated = build_earnings_statement(
        _snapshot(income=quiet_income, realized=quiet_realized, notes=[_truncation_note()])
    )

    assert complete["posture"] == "empty"
    assert truncated["posture"] == "ready"
    assert truncated["completeness"] == "window_truncated"


def test_missing_evidence_is_said_rather_than_summed():
    """A section that was ordered but carries no summaries must not become a
    statement of zeros that looks whole - a fact about the data, said."""

    snapshot = {"incomeAndActivity": {"supportability": {"status": "partial", "notes": []}}}

    statement = build_earnings_statement(snapshot)

    assert statement["posture"] == "unavailable"
    assert "income" not in statement


def test_the_by_type_split_appears_only_when_the_capture_carries_it():
    """A snapshot taken before the split was forwarded gets a statement
    without it, rather than one reconstructed from data the package does not
    hold - a rerender must not invent evidence its capture never established."""

    with_split = build_earnings_statement(
        _snapshot(
            income=_income_summary(
                by_type={
                    "DIVIDEND": {
                        "transaction_count": 8,
                        "net_amount_reporting_currency": 15000.0,
                    },
                    "INTEREST": {
                        "transaction_count": 1,
                        "net_amount_reporting_currency": 1209.6,
                    },
                }
            )
        )
    )
    without_split = build_earnings_statement(_snapshot(income=_income_summary()))

    assert [entry["income_type"] for entry in with_split["income"]["by_type"]] == [
        "DIVIDEND",
        "INTEREST",
    ]
    assert "by_type" not in without_split["income"]


def test_the_statement_says_it_is_not_a_tax_document():
    """Tax-lot jurisdiction treatment is not sourced, and the methodology
    travels with the money so the page cannot imply tax-reportable numbers."""

    statement = build_earnings_statement(_snapshot())

    assert statement["methodology"]["tax_lot_jurisdiction_treatment"] == "not_sourced"
    assert statement["methodology"]["basis"] == "transaction_level_realized_gain_loss"


def test_a_report_that_did_not_order_transactions_promises_nothing():
    assert build_earnings_statement({}) == {}
