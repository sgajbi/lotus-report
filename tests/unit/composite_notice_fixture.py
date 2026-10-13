"""Immutable 8d captures for tests; never a runtime source or database seed."""

import base64
import gzip
import hashlib
import json
from pathlib import Path

from app.composite_reporting.correction_notice.admission import build_notice
from app.composite_reporting.correction_notice.evidence import acknowledged_path
from app.composite_reporting.correction_notice.selection import (
    SCOPE_AXES,
    CorrectionNoticeSelection,
    SelectionTuple,
)
from app.composite_reporting.models import CompositeReviewJobRequest
from app.reporting_identity.capture_binding import revision_for_capture
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_lineage.models import ReportInputSnapshotRecord
from app.reporting_lineage.store import compute_snapshot_hash

FIXTURES = Path(__file__).parents[1] / "fixtures" / "composite-correction-notice"


def retained_bytes(name: str) -> bytes:
    envelope = json.loads((FIXTURES / (name + ".json")).read_text())
    raw = gzip.decompress(base64.b64decode(envelope["gzip_base64"]))
    assert hashlib.sha256(raw).hexdigest() == envelope["sha256"]
    return raw


def retained(name: str):
    return json.loads(retained_bytes(name))


def notice_fixture():
    generations = {}
    for side, name in (
        ("before", "consumer-0076-snapshot-original-response.bin"),
        ("after", "consumer-0094-snapshot-corrected-response.bin"),
    ):
        raw = retained_bytes(name)
        snapshot = json.loads(raw)
        payload = snapshot["snapshot_payload"]
        generations[side] = {
            "selection": {key: snapshot[key] for key in SelectionTuple.model_fields},
            "contract_version": "composite_review.v8",
            "snapshot_response_bytes_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
            "payload": payload,
            "source_revision_vector": snapshot["source_revision_vector"],
            "source_identity": {key: payload["selection"][key] for key in SCOPE_AXES},
            "capture_identity": {
                key: snapshot[key]
                for key in (
                    "report_revision_id",
                    "series_digest",
                    "source_revision_digest",
                    "factual_content_digest",
                )
            },
        }
    documents = [
        retained(f"consumer-{112 + index:04d}-final-chain-{index}-response.bin")
        for index in range(4)
    ]
    # Controlled record fixture only, never an Archive197 API response. Original
    # historical POST bytes remain separately retained without relabelling.
    relationship = {
        key: value
        for key, value in retained("consumer-0106-archive-correction-response.bin").items()
        if key != "current_document_id"
    }
    events = {
        "before": retained("consumer-0105-source-off-events-original-response.bin")["events"],
        "after": retained("consumer-0111-source-off-events-corrected-response.bin")["events"],
    }
    technical = [
        next(
            event for event in events[side] if event["event_type"] == "job_archive_lineage_recorded"
        )
        for side in ("before", "after")
    ]
    scope = generations["before"]["source_identity"]
    selection = CorrectionNoticeSelection.model_validate(
        {
            "comparison_profile": "RETAINED_SAME_SCOPE_CORRECTION_NOTICE_V1",
            **{
                key: scope[key]
                for key in (
                    "tenant_id",
                    "composite_id",
                    "period_start",
                    "period_end",
                    "reporting_currency",
                )
            },
            **{side: generation["selection"] for side, generation in generations.items()},
            "source_document_ids": [document["document_id"] for document in documents],
            "relationship_ids": [
                technical[0]["event_payload"]["lifecycle_relationship_id"],
                relationship["lifecycle_relationship_id"],
                technical[1]["event_payload"]["lifecycle_relationship_id"],
            ],
        }
    )
    path = acknowledged_path(
        selection=selection,
        generations=generations,
        documents=documents,
        relationship_record=relationship,
        report_events=events,
        relationship_source_capture="CONTROLLED_TEST_RELATIONSHIP_RECORD_NOT_API_READ",
    )
    # Preserve the prior candidate's technical capture labels in this historical
    # comparison fixture. Runtime capture uses exact durable status-event IDs.
    for index in (0, 2):
        path["edges"][index]["acknowledgement"]["source_capture"] = f"8d:edge:{index}"
    return build_notice(tenant_id=scope["tenant_id"], generations=generations, archive_path=path)


def notice_job_and_capture(notice):
    """Mint a new test notice job; its identity is never borrowed from v8."""
    scope = notice["generations"]["before"]["source_identity"]
    selection = {
        "comparison_profile": notice["comparison_profile"],
        **{
            key: scope[key]
            for key in (
                "tenant_id",
                "composite_id",
                "period_start",
                "period_end",
                "reporting_currency",
            )
        },
        "before": notice["generations"]["before"]["selection"],
        "after": notice["generations"]["after"]["selection"],
        "source_document_ids": [node["document_id"] for node in notice["archive_path"]["nodes"]],
        "relationship_ids": [edge["relationship_id"] for edge in notice["archive_path"]["edges"]],
    }
    job = ReportJobLedgerRecord(
        request_id="rrq_notice_test",
        job_id="rjob_notice_test",
        report_type="composite_review",
        portfolio_scope={"composite_id": scope["composite_id"]},
        requested_output_formats=["xlsx"],
        as_of_date=scope["period_end"],
        reporting_currency=scope["reporting_currency"],
        options={"composite_notice_selection": selection},
        trigger_type="manual",
        triggered_by="test",
        caller_application="test",
        tenant_id=scope["tenant_id"],
        region="APAC",
        idempotency_key="notice-test",
        request_hash="test",
        status="data_ready",
        current_step="data_ready",
        retry_eligible=False,
        cancel_requested=False,
        created_at="2026-10-12T00:00:00Z",
        updated_at="2026-10-12T00:00:00Z",
        correlation_id="notice-test",
        trace_id="notice-test",
        render_template_id="composite-review",
        render_template_version="v9",
        render_template_publication="development",
        accepted_document_contract={
            "report_data_contract_version": "composite_review.v9",
            "input_snapshot_contract_version": "composite_review.v9",
        },
    )
    identity, vector = revision_for_capture(
        job=job, snapshot_payload=notice, upstream_services=("lotus-archive", "lotus-report")
    )
    record = ReportInputSnapshotRecord(
        snapshot_id="rsnap_notice_test",
        report_job_id=job.job_id,
        report_type=job.report_type,
        report_data_contract_version="composite_review.v9",
        portfolio_scope=job.portfolio_scope,
        as_of_date=job.as_of_date,
        snapshot_payload=notice,
        snapshot_hash=compute_snapshot_hash(notice),
        report_revision_id=identity.report_revision_id,
        series_digest=identity.series_digest,
        source_revision_digest=identity.source_revision_digest,
        factual_content_digest=identity.factual_content_digest,
        source_revision_vector=vector.canonical(),
        supportability_status="partial",
        completeness_status="partial",
        lineage_summary={},
        captured_at=job.created_at,
        created_at=job.created_at,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
    )
    return job, record


def retained_source_job_and_capture(side, notice):
    """Test-only job reconstruction from the retained native request/status.

    Runtime code must read the durable ledger. This fixture cannot establish
    runtime authority and is deliberately kept outside the application package.
    """
    names = (
        (
            "consumer-0071-report-order-original-request.bin",
            "consumer-0075-job-original-74-response.bin",
            "consumer-0076-snapshot-original-response.bin",
        )
        if side == "before"
        else (
            "consumer-0089-report-order-corrected-request.bin",
            "consumer-0093-job-corrected-92-response.bin",
            "consumer-0094-snapshot-corrected-response.bin",
        )
    )
    request = CompositeReviewJobRequest.model_validate(retained(names[0]))
    status = retained(names[1])
    raw = retained_bytes(names[2])
    record = ReportInputSnapshotRecord.model_validate_json(raw)
    job, _ = notice_job_and_capture(notice)
    job = job.model_copy(
        update={
            "job_id": record.report_job_id,
            "request_id": status["report_request_id"],
            "options": request.capture_options(),
            "render_template_version": "v8",
            "accepted_document_contract": {
                "report_data_contract_version": "composite_review.v8",
                "input_snapshot_contract_version": "composite_review.v8",
            },
        }
    )
    return job, record, raw
