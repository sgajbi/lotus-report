from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from app.report_batch_orchestrator.models import (
    BatchItemStatusResponse,
    BatchStatusResponse,
    ReportBatchItemRecord,
    ReportBatchRecord,
)
from app.reporting_jobs.models import ReportJobArchiveStatusRecord


class ReportJobArchiveStatusLookup(Protocol):
    def get_archive_statuses_by_job_ids(
        self,
        job_ids: list[str],
        *,
        tenant_id: str,
    ) -> list[ReportJobArchiveStatusRecord]: ...


def load_report_job_archive_statuses(
    items: Iterable[ReportBatchItemRecord],
    *,
    report_job_lookup: ReportJobArchiveStatusLookup,
    tenant_id: str,
) -> dict[str, ReportJobArchiveStatusRecord]:
    """Resolve linked report jobs for a batch, scoped to the admitted batch tenant.

    A batch item's report_job_id was written by whichever worker dispatched it, so durable
    state predating tenant-scoped dispatch can link a batch to another tenant's job. Passing
    batch admission says nothing about the job on the other end of that link, so the tenant
    travels with the lookup: a foreign job is not returned, and therefore neither its
    lifecycle status nor its archive_document_id can reach the response.
    """

    job_ids = sorted({item.report_job_id for item in items if item.report_job_id})
    if not job_ids:
        return {}
    return {
        record.report_job_id: record
        for record in report_job_lookup.get_archive_statuses_by_job_ids(
            job_ids,
            tenant_id=tenant_id,
        )
    }


def build_batch_item_status(
    item: ReportBatchItemRecord,
    *,
    report_job: ReportJobArchiveStatusRecord | None = None,
) -> BatchItemStatusResponse:
    linked_report_job = (
        report_job
        if (
            item.report_job_id is not None
            and report_job is not None
            and report_job.report_job_id == item.report_job_id
        )
        else None
    )
    return BatchItemStatusResponse(
        batch_item_id=item.batch_item_id,
        item_position=item.item_position,
        portfolio_id=item.portfolio_id,
        status=item.status,
        report_job_id=item.report_job_id,
        report_job_status=linked_report_job.status if linked_report_job else None,
        archive_document_id=(
            linked_report_job.archive_document_id
            if linked_report_job and linked_report_job.status == "archived"
            else None
        ),
        attempt_count=item.attempt_count,
        retry_eligible=item.retry_eligible,
        next_retry_at=item.next_retry_at,
        last_error_category=item.last_error_category,
        last_error_summary=item.last_error_summary,
        created_at=item.created_at,
        started_at=item.started_at,
        completed_at=item.completed_at,
        cancelled_at=item.cancelled_at,
    )


def build_batch_status(
    record: ReportBatchRecord,
    *,
    report_jobs_by_id: dict[str, ReportJobArchiveStatusRecord],
) -> BatchStatusResponse:
    status_counts: dict[str, int] = {}
    for item in record.items:
        status_counts[item.status] = status_counts.get(item.status, 0) + 1
    return BatchStatusResponse(
        batch_id=record.batch_id,
        selector_mode=record.selector_mode,
        tenant_id=record.tenant_id,
        region=record.region,
        materialized_portfolio_ids=record.materialized_portfolio_ids,
        as_of_date=record.as_of_date,
        requested_output_formats=record.requested_output_formats,
        reporting_currency=record.reporting_currency,
        status=record.status,
        item_count=record.item_count,
        status_counts=status_counts,
        items=[
            build_batch_item_status(
                item,
                report_job=(
                    report_jobs_by_id.get(item.report_job_id) if item.report_job_id else None
                ),
            )
            for item in record.items
        ],
        created_at=record.created_at,
        updated_at=record.updated_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        cancelled_at=record.cancelled_at,
        failed_at=record.failed_at,
        correlation_id=record.correlation_id,
        trace_id=record.trace_id,
    )
