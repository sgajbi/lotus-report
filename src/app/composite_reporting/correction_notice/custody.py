"""Notice own identity stays separate from immutable source-generation anchors."""

from typing import Any

from app.composite_reporting.correction_notice.admission import require_equal
from app.composite_reporting.correction_notice.contract import (
    CorrectionNoticeData,
    OwnNoticeCustodyBinding,
)
from app.composite_reporting.correction_notice.selection import CorrectionNoticeSelection
from app.reporting_identity.capture_binding import revision_for_capture
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_lineage.models import ReportInputSnapshotRecord
from app.reporting_lineage.store import compute_snapshot_hash


def notice_archive_custody(
    *, job: ReportJobLedgerRecord, record: ReportInputSnapshotRecord
) -> dict[str, Any]:
    data = CorrectionNoticeData.model_validate(record.snapshot_payload)
    scope = data.generations.before.source_identity
    ordered = CorrectionNoticeSelection.model_validate(
        job.options.get("composite_notice_selection")
    )
    require_equal(
        ordered.model_dump(mode="json"),
        {
            "comparison_profile": data.comparison_profile,
            "tenant_id": scope.tenant_id,
            "composite_id": scope.composite_id,
            "period_start": scope.period_start,
            "period_end": scope.period_end,
            "reporting_currency": scope.reporting_currency,
            "before": data.generations.before.selection.model_dump(),
            "after": data.generations.after.selection.model_dump(),
            "source_document_ids": [node.document_id for node in data.archive_path.nodes],
            "relationship_ids": [edge.relationship_id for edge in data.archive_path.edges],
        },
        "OWN_ORDER_SELECTION",
    )
    require_equal(record.report_type, "composite_review", "OWN_FAMILY")
    require_equal(job.report_type, "composite_review", "OWN_FAMILY")
    require_equal(record.report_data_contract_version, "composite_review.v9", "OWN_CONTRACT")
    require_equal(record.snapshot_hash, compute_snapshot_hash(record.snapshot_payload), "OWN_HASH")
    require_equal(record.report_job_id, job.job_id, "OWN_JOB")
    require_equal(record.portfolio_scope, job.portfolio_scope, "OWN_SCOPE")
    require_equal(job.portfolio_scope, {"composite_id": scope.composite_id}, "OWN_SCOPE")
    require_equal(
        (job.tenant_id, job.as_of_date.isoformat(), job.reporting_currency),
        (scope.tenant_id, scope.period_end, scope.reporting_currency),
        "OWN_SCOPE",
    )
    require_equal(record.as_of_date, job.as_of_date, "OWN_DATE")
    contract = job.accepted_document_contract or {}
    for key in ("report_data_contract_version", "input_snapshot_contract_version"):
        require_equal(contract.get(key), "composite_review.v9", "OWN_CONTRACT")
    require_equal(
        (job.render_template_id, job.render_template_version),
        ("composite-review", "v9"),
        "OWN_TEMPLATE",
    )
    binding = revision_for_capture(
        job=job,
        snapshot_payload=record.snapshot_payload,
        upstream_services=("lotus-archive", "lotus-report"),
    )
    if binding is None:
        raise ValueError("COMPOSITE_NOTICE_OWN_REVISION_REQUIRED")
    identity, vector = binding
    for key in (
        "report_revision_id",
        "series_digest",
        "source_revision_digest",
        "factual_content_digest",
    ):
        require_equal(getattr(record, key), getattr(identity, key), "OWN_REVISION_CONFLICT")
    require_equal(record.source_revision_vector, vector.canonical(), "OWN_VECTOR")
    if record.report_revision_id in (
        data.generations.before.selection.report_revision_id,
        data.generations.after.selection.report_revision_id,
    ):
        raise ValueError("COMPOSITE_NOTICE_SOURCE_IDENTITY_REUSED")
    payload = record.snapshot_payload
    generations = {}
    for side, index in (("before", 0), ("after", 2)):
        node = payload["archive_path"]["nodes"][index]
        generations[side] = {
            **payload["generations"][side]["selection"],
            "source_document_id": node["document_id"],
            "source_identity": node["document_response"]["composite_report_identity"],
        }
    own = OwnNoticeCustodyBinding.model_validate(
        {
            **{
                key: payload[key]
                for key in (
                    "contract_version",
                    "comparison_profile",
                    "qualification",
                    "publication_state",
                    "notice_completeness",
                    "pair_fingerprint",
                    "manifest_digest",
                )
            },
            "generations": generations,
            **{
                key: getattr(identity, key).removeprefix("sha256:")
                for key in ("series_digest", "source_revision_digest", "factual_content_digest")
            },
        }
    )
    result: dict[str, Any] = {
        "report_request_id": job.request_id,
        "portfolio_scope": "composite",
        "portfolio_id": None,
        "composite_id": scope.composite_id,
        "as_of_date": scope.period_end,
        "reporting_period_start": scope.period_start,
        "reporting_period_end": scope.period_end,
        "frequency": "ad_hoc",
        "classification": "confidential",
        "region": job.region,
        "tenant_id": job.tenant_id,
        "retention_start_date": job.as_of_date.isoformat(),
        "report_revision_id": record.report_revision_id,
        "composite_report_identity": own.model_dump(mode="json"),
    }
    for key in ("retention_policy_id", "retain_until_date"):
        if job.options.get(key):
            result[key] = job.options[key]
    return result
