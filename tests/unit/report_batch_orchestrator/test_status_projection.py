from datetime import UTC, datetime

from app.report_batch_orchestrator.models import ReportBatchItemRecord
from app.report_batch_orchestrator.status_projection import (
    build_batch_item_status,
    load_report_job_archive_statuses,
)
from app.reporting_jobs.models import ReportJobArchiveStatusRecord


def _item(*, report_job_id: str | None = "rjob_linked") -> ReportBatchItemRecord:
    return ReportBatchItemRecord(
        batch_item_id="rbci_1",
        batch_id="rbch_1",
        item_position=1,
        portfolio_id="PB_SG_GLOBAL_BAL_001",
        item_idempotency_key="batch-item-1",
        status="waiting_on_report_job",
        source_system="lotus-core",
        source_object="PortfolioScope",
        created_at=datetime(2026, 4, 22, 9, 0, tzinfo=UTC),
        report_job_id=report_job_id,
    )


def test_archived_document_requires_matching_archived_report_job() -> None:
    archived = ReportJobArchiveStatusRecord(
        report_job_id="rjob_linked",
        status="archived",
        archive_document_id="doc_source_owned",
    )

    response = build_batch_item_status(_item(), report_job=archived)

    assert response.report_job_status == "archived"
    assert response.archive_document_id == "doc_source_owned"


def test_non_archived_and_foreign_jobs_cannot_publish_document_identity() -> None:
    pending_with_unexpected_document = ReportJobArchiveStatusRecord(
        report_job_id="rjob_linked",
        status="archiving",
        archive_document_id="doc_must_not_leak",
    )
    foreign_archived = ReportJobArchiveStatusRecord(
        report_job_id="rjob_foreign",
        status="archived",
        archive_document_id="doc_foreign",
    )

    pending_response = build_batch_item_status(
        _item(),
        report_job=pending_with_unexpected_document,
    )
    foreign_response = build_batch_item_status(_item(), report_job=foreign_archived)

    assert pending_response.report_job_status == "archiving"
    assert pending_response.archive_document_id is None
    assert foreign_response.report_job_status is None
    assert foreign_response.archive_document_id is None


def test_archive_status_lookup_is_deduplicated_and_omits_unlinked_items() -> None:
    class _Lookup:
        requested_job_ids: list[str] = []

        requested_tenant_id: str = ""

        def get_archive_statuses_by_job_ids(
            self,
            job_ids: list[str],
            *,
            tenant_id: str,
        ) -> list[ReportJobArchiveStatusRecord]:
            self.requested_job_ids = job_ids
            self.requested_tenant_id = tenant_id
            return [
                ReportJobArchiveStatusRecord(
                    report_job_id="rjob_linked",
                    status="accepted",
                )
            ]

    lookup = _Lookup()

    result = load_report_job_archive_statuses(
        [_item(), _item(), _item(report_job_id=None)],
        report_job_lookup=lookup,
        tenant_id="tenant-sg",
    )

    assert lookup.requested_job_ids == ["rjob_linked"]
    assert lookup.requested_tenant_id == "tenant-sg"
    assert list(result) == ["rjob_linked"]
