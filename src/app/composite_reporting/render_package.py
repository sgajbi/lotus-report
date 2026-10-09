"""Composite custody from persisted Report identity, without portfolio substitutes."""

from typing import Any

from app.composite_reporting.product_contract import validate_composite_dataset
from app.reporting_document_format import document_output_format
from app.reporting_identity.capture_binding import revision_for_capture
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_lineage.models import ReportInputSnapshotRecord


def build_composite_render_package(
    *,
    job: ReportJobLedgerRecord,
    snapshot: dict[str, Any],
    render_job_id: str,
    snapshot_id: str,
    report_revision_id: str | None,
    snapshot_record: ReportInputSnapshotRecord | None,
) -> dict[str, Any]:
    from app.reporting_render.package_builder import (
        _job_disclosure_baseline,
        _job_report_data_contract,
        _render_package_envelope,
    )

    if document_output_format(job.requested_output_formats) != "xlsx":
        raise ValueError("COMPOSITE_RENDER_XLSX_REQUIRED")
    if (
        snapshot_record is None
        or snapshot_record.snapshot_id != snapshot_id
        or snapshot_record.snapshot_payload != snapshot
        or snapshot_record.report_revision_id != report_revision_id
    ):
        raise ValueError("COMPOSITE_RENDER_PERSISTED_SNAPSHOT_REQUIRED")
    custody = composite_archive_custody(job=job, record=snapshot_record)
    package = _render_package_envelope(
        job=job,
        snapshot=snapshot,
        render_job_id=render_job_id,
        snapshot_id=snapshot_id,
        report_revision_id=report_revision_id,
        report_data_contract_version=_job_report_data_contract(job),
        report_data=snapshot,
        lineage_refs=[snapshot_id, report_revision_id or ""],
        disclosure_refs=[_job_disclosure_baseline(job)],
        archive_custody=custody,
    )
    if snapshot["contract_version"] in {
        "composite_review.v4",
        "composite_review.v5",
        "composite_review.v6",
    }:
        from app.composite_reporting.eligibility_tables import preflight_eligibility_package

        prefix = (
            "COMPOSITE_POOLED"
            if snapshot["contract_version"] == "composite_review.v5"
            else "COMPOSITE_AMENDMENT"
            if snapshot["contract_version"] == "composite_review.v6"
            else ("COMPOSITE_ELIGIBILITY")
        )
        preflight_eligibility_package(package, failure_prefix=prefix)
    return package


def composite_archive_custody(
    *, job: ReportJobLedgerRecord, record: ReportInputSnapshotRecord
) -> dict[str, Any]:
    data = validate_composite_dataset(record.snapshot_payload)
    selection = data.selection
    _require_custody_contract(job, record, data.contract_version)
    if (
        record.report_job_id != job.job_id
        or record.report_type != job.report_type
        or record.portfolio_scope != job.portfolio_scope
        or job.portfolio_scope != {"composite_id": selection.composite_id}
        or selection.tenant_id != job.tenant_id
        or record.as_of_date != job.as_of_date
        or job.as_of_date != selection.period_end
        or job.reporting_currency != selection.reporting_currency
    ):
        raise ValueError("COMPOSITE_CUSTODY_JOB_IDENTITY_MISMATCH")
    binding = revision_for_capture(
        job=job,
        snapshot_payload=record.snapshot_payload,
        upstream_services=("lotus-manage",)
        if data.contract_version in {"composite_review.v4", "composite_review.v6"}
        else ("lotus-performance",),
    )
    if binding is None:
        raise ValueError("COMPOSITE_CUSTODY_REVISION_REQUIRED")
    identity, vector = binding
    fields = (
        "report_revision_id",
        "series_digest",
        "source_revision_digest",
        "factual_content_digest",
    )
    if any(getattr(record, field) != getattr(identity, field) for field in fields) or (
        record.source_revision_vector != vector.canonical()
    ):
        raise ValueError("COMPOSITE_CUSTODY_REVISION_CONFLICT")
    # Every persisted digest has already matched the independently derived
    # revision. Normalize those verified identity values for Archive's wire
    # contract; a second spelling guard would have no reachable bad input.
    digests = {field: getattr(identity, field).removeprefix("sha256:") for field in fields[1:]}
    custody: dict[str, Any] = {
        "report_request_id": job.request_id,
        "portfolio_scope": "composite",
        "portfolio_id": None,
        "composite_id": selection.composite_id,
        "as_of_date": selection.period_end.isoformat(),
        "reporting_period_start": selection.period_start.isoformat(),
        "reporting_period_end": selection.period_end.isoformat(),
        "frequency": "ad_hoc",
        "classification": "confidential",
        "region": job.region,
        "tenant_id": job.tenant_id,
        "retention_start_date": job.as_of_date.isoformat(),
        "report_revision_id": record.report_revision_id,
        "composite_report_identity": {
            "contract_version": data.contract_version,
            "qualification": data.qualification,
            "publication_state": data.publication_state,
            "selection": selection.model_dump(mode="json"),
            **digests,
        },
    }
    for field in ("retention_policy_id", "retain_until_date"):
        value = job.options.get(field)
        if value:
            custody[field] = value
    if data.contract_version == "composite_review.v2":
        custody["composite_report_identity"]["source_products"] = [
            {
                "pin": product.pin.model_dump(mode="json"),
                "source_response_digest": product.source_response_digest,
            }
            for product in data.source_products
        ]
    return custody


def _require_custody_contract(
    job: ReportJobLedgerRecord,
    record: ReportInputSnapshotRecord,
    contract_version: str,
) -> None:
    accepted = job.accepted_document_contract or {}
    axes = (
        record.report_data_contract_version,
        accepted.get("report_data_contract_version", contract_version),
        accepted.get("input_snapshot_contract_version", contract_version),
    )
    if any(axis != contract_version for axis in axes):
        raise ValueError("COMPOSITE_CUSTODY_CONTRACT_CONFLICT")
