"""Transaction page evidence is preserved through the real report paging loop."""

from copy import deepcopy

import pytest

from app.config import settings
from app.services.reporting_read_service import ReportingReadService
from tests.unit.test_reporting_read_service_additional import (
    _CoreQuerySuccessMinimal,
    _PerformanceSuccessEmpty,
    _RiskSuccess,
    _transaction_ledger_metadata,
)


class _TransactionPages(_CoreQuerySuccessMinimal):
    def __init__(self, pages, *, missing_portfolio_page=None):
        self.pages = pages
        self.reads = []
        self.missing_portfolio_page = missing_portfolio_page

    async def get_portfolio_transactions(
        self, portfolio_id, params, correlation_id=None, *, admitted_tenant_id=""
    ):
        page = len(self.reads)
        self.reads.append((params["skip"], admitted_tenant_id))
        payload = {
            "portfolio_id": portfolio_id,
            "reporting_currency": "USD",
            **deepcopy(self.pages[page]),
            "total": 2,
            "skip": page,
            "limit": 1,
            "transactions": [
                {
                    "transaction_id": f"TX-{page}",
                    "transaction_date": "2026-02-03",
                    "transaction_type": "BUY",
                    "security_id": "EQ-1",
                    "gross_transaction_amount_reporting_currency": 10,
                }
            ],
        }
        if page == self.missing_portfolio_page:
            payload.pop("portfolio_id")
        return 200, payload


async def _read(pages, *, missing_portfolio_page=None):
    client = _TransactionPages(pages, missing_portfolio_page=missing_portfolio_page)
    service = ReportingReadService(
        core_query_client=client,
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    result = await service._list_transaction_rows_result(
        portfolio_id="P1",
        params={"limit": 1},
        correlation_id="caller",
        admitted_tenant_id="default",
    )
    assert [row["transaction_id"] for row in result.rows] == ["TX-0", "TX-1"]
    assert client.reads == [(0, "default"), (1, "default")]
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [0, 1])
@pytest.mark.parametrize("value", [None, "", " ", 42, {"private": "customer-detail"}])
async def test_invalid_required_portfolio_identity_on_any_page_remains_partial(page, value):
    pages = [_transaction_ledger_metadata(), _transaction_ledger_metadata()]
    pages[page]["portfolio_id"] = value
    result = await _read(pages)
    assert result.supportability["status"] == "partial"
    note = next(
        note
        for note in result.supportability["notes"]
        if note["code"] == "transaction_window_trust_metadata_incomplete"
    )
    assert "portfolio_id" in note["missing_fields"] and note["pages"] == [page + 1]
    assert "customer-detail" not in str(result.source_product)


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [0, 1])
async def test_absent_required_portfolio_identity_on_any_page_remains_partial(page):
    result = await _read(
        [_transaction_ledger_metadata(), _transaction_ledger_metadata()],
        missing_portfolio_page=page,
    )
    assert result.supportability["status"] == "partial"
    assert result.source_product["page_evidence"][page]["missing_fields"] == ["portfolio_id"]


@pytest.mark.asyncio
async def test_optional_raw_ledger_currency_none_is_a_valid_coherent_scope():
    pages = [_transaction_ledger_metadata(), _transaction_ledger_metadata()]
    for page in pages:
        page["reporting_currency"] = None
    result = await _read(pages)
    assert result.supportability == {"status": "ready", "notes": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_raw_and_restatement_currency_scopes_cannot_be_combined(reverse):
    pages = [_transaction_ledger_metadata(), _transaction_ledger_metadata()]
    pages[0]["reporting_currency"] = None
    pages[1]["reporting_currency"] = "USD"
    if reverse:
        pages.reverse()
    result = await _read(pages)
    assert result.supportability["status"] == "partial"
    assert result.source_product["reporting_currency"] == pages[0]["reporting_currency"]
    assert any(
        "reporting_currency" in note.get("fields", []) for note in result.supportability["notes"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_degraded_page_cannot_be_healed_by_another_consumed_page(reverse):
    bad = _transaction_ledger_metadata(
        data_quality_status="PARTIAL", reason_codes=["MISSING_REFERENCE"]
    )
    bad["reconciliation_status"] = "UNRECONCILED"
    healthy = _transaction_ledger_metadata()
    result = await _read([healthy, bad] if reverse else [bad, healthy])
    assert result.supportability["status"] == "partial"
    notes = {note["code"]: note for note in result.supportability["notes"]}
    assert "transaction_window_source_quality_not_complete" in notes
    assert "transaction_window_reconciliation_not_complete" in notes
    assert "MISSING_REFERENCE" in result.source_product["reason_codes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_page", [0, 1])
async def test_missing_required_page_metadata_cannot_be_filled_by_another_page(missing_page):
    pages = [_transaction_ledger_metadata(), _transaction_ledger_metadata()]
    pages[missing_page].pop("snapshot_id")
    result = await _read(pages)
    assert result.supportability["status"] == "partial"
    note = next(
        note
        for note in result.supportability["notes"]
        if note["code"] == "transaction_window_trust_metadata_incomplete"
    )
    assert "snapshot_id" in note["missing_fields"]
    assert note["pages"] == [missing_page + 1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "snapshot_id",
        "portfolio_id",
        "tenant_id",
        "reporting_currency",
        "policy_version",
        "restatement_version",
        "source_batch_fingerprint",
        "latest_evidence_timestamp",
        "as_of_date",
        "product_name",
        "product_version",
    ],
)
async def test_stable_revision_drift_is_explicitly_incoherent(field):
    pages = [_transaction_ledger_metadata(), _transaction_ledger_metadata()]
    pages[1][field] = "foreign-reconstruction-scope"
    result = await _read(pages)
    assert result.supportability["status"] == "partial"
    note = next(
        note
        for note in result.supportability["notes"]
        if note["code"] == "transaction_window_source_identity_incoherent"
    )
    assert note["fields"] == [field] and note["pages"] == [2]
    assert "foreign-reconstruction-scope" not in str(note)


@pytest.mark.asyncio
async def test_distinct_page_hashes_do_not_create_a_false_revision_conflict():
    pages = [_transaction_ledger_metadata(), _transaction_ledger_metadata()]
    pages[0]["content_hash"], pages[1]["content_hash"] = "sha256:page-one", "sha256:page-two"
    pages[1]["generated_at"] = "2026-02-24T10:01:00Z"
    pages[1]["correlation_id"] = "different-operational-correlation"
    result = await _read(pages)
    assert result.supportability == {"status": "ready", "notes": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["data_quality_status", "reconciliation_status"])
async def test_freeform_status_is_unknown_without_publishing_customer_text(field):
    pages = [_transaction_ledger_metadata(), _transaction_ledger_metadata()]
    pages[0][field] = "customer private detail"
    result = await _read(pages)
    assert result.supportability["status"] == "partial"
    assert result.source_product[field] == "UNKNOWN"
    assert "customer private detail" not in str(result.source_product)


@pytest.mark.asyncio
async def test_source_reasons_are_deduplicated_independently_of_page_order():
    bad = _transaction_ledger_metadata(
        data_quality_status="PARTIAL", reason_codes=["REF_B", "REF_A", "REF_A"]
    )
    other = _transaction_ledger_metadata(
        data_quality_status="PARTIAL", reason_codes=["REF_C", "REF_B"]
    )
    forward, reverse = await _read([bad, other]), await _read([other, bad])
    assert (
        forward.source_product["reason_codes"]
        == reverse.source_product["reason_codes"]
        == ["REF_A", "REF_B", "REF_C"]
    )


@pytest.mark.asyncio
async def test_actual_public_transaction_section_retains_the_aggregate_limitation():
    bad = _transaction_ledger_metadata(
        data_quality_status="PARTIAL", reason_codes=["MISSING_REFERENCE"]
    )
    client = _TransactionPages([bad, _transaction_ledger_metadata()])
    service = ReportingReadService(
        core_query_client=client,
        performance_client=_PerformanceSuccessEmpty(),
        risk_client=_RiskSuccess(),
    )
    report = await service.get_portfolio_review(
        "P1",
        {"as_of_date": "2026-02-24", "sections": ["TRANSACTIONS"]},
        None,
        admitted_tenant_id="default",
    )
    assert report["transactions"]["transactionCount"] == 2
    assert report["transactions"]["supportability"]["status"] == "partial"
    assert report["transactions"]["sourceProduct"]["reason_codes"] == [
        "MISSING_REFERENCE",
        "TRANSACTION_LEDGER_READY",
    ]


@pytest.mark.asyncio
async def test_page_budget_keeps_existing_truncation_warning(monkeypatch):
    monkeypatch.setattr(settings, "report_transaction_max_pages", 1)
    client = _TransactionPages([_transaction_ledger_metadata()])
    service = ReportingReadService(
        core_query_client=client, performance_client=None, risk_client=None
    )
    result = await service._list_transaction_rows_result(
        portfolio_id="P1",
        params={"limit": 1},
        correlation_id="caller",
        admitted_tenant_id="default",
    )
    assert len(result.rows) == 1 and len(client.reads) == 1
    assert result.supportability["status"] == "partial"
    assert any(
        note["code"] == "transaction_window_truncated" for note in result.supportability["notes"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("snapshot_id", {"private": "customer-detail"}),
        ("tenant_id", " "),
        ("data_quality_status", None),
    ],
)
async def test_malformed_required_metadata_is_explicit_and_does_not_publish_raw_values(
    field, value
):
    pages = [_transaction_ledger_metadata(), _transaction_ledger_metadata()]
    pages[0][field] = value
    result = await _read(pages)
    assert result.supportability["status"] == "partial"
    assert "customer-detail" not in str(result.source_product)
    assert any(field in note.get("missing_fields", []) for note in result.supportability["notes"])


@pytest.mark.asyncio
async def test_unsafe_or_excessive_source_reasons_are_bounded_and_explicit():
    pages = [_transaction_ledger_metadata(), _transaction_ledger_metadata()]
    pages[0]["reason_codes"] = ["customer private detail"] + [
        f"SOURCE_REASON_{index:03}" for index in range(100)
    ]
    result = await _read(pages)
    assert result.supportability["status"] == "partial"
    assert "customer private detail" not in str(result.source_product)
    assert len(result.source_product["reason_codes"]) <= 64
    assert any(
        note["code"] == "transaction_window_reason_evidence_bounded"
        for note in result.supportability["notes"]
    )
