"""Verify source captures through existing native v8 derivation before notice capture."""

from copy import deepcopy
from hashlib import sha256
from typing import Any

from app.composite_reporting.correction_notice.admission import (
    require_equal,
    source_custody_identity,
)
from app.composite_reporting.correction_notice.contract import Generation
from app.composite_reporting.correction_notice.selection import SCOPE_AXES, SelectionTuple
from app.composite_reporting.render_package import composite_archive_custody
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_lineage.models import ReportInputSnapshotRecord
from app.reporting_lineage.store import compute_snapshot_hash


def generation_from_capture(
    *,
    selection: SelectionTuple,
    source_job: ReportJobLedgerRecord,
    snapshot: ReportInputSnapshotRecord,
    response_bytes: bytes,
    tenant_id: str,
    region: str,
) -> dict[str, Any]:
    import json

    # Authorize persisted scope before comparing identity or disclosing source existence.
    require_equal(
        (source_job.tenant_id, source_job.region), (tenant_id, region), "SOURCE_ACCESS_REFUSED"
    )
    require_equal(source_job.report_type, "composite_review", "SOURCE_FAMILY")
    require_equal(snapshot.report_data_contract_version, "composite_review.v8", "SOURCE_VERSION")
    require_equal(
        {key: getattr(snapshot, key) for key in SelectionTuple.model_fields},
        selection.model_dump(),
        "SOURCE_CAPTURE_SELECTION",
    )
    require_equal(
        compute_snapshot_hash(snapshot.snapshot_payload), snapshot.snapshot_hash, "SOURCE_HASH"
    )
    raw = json.loads(response_bytes)
    require_equal(raw["snapshot_payload"], snapshot.snapshot_payload, "SOURCE_RAW_PAYLOAD")
    for key in (
        "report_job_id",
        "snapshot_id",
        "snapshot_hash",
        "report_revision_id",
        "series_digest",
        "source_revision_digest",
        "factual_content_digest",
        "source_revision_vector",
    ):
        require_equal(raw[key], getattr(snapshot, key), "SOURCE_RAW_IDENTITY")
    # This invokes existing revision_for_capture and compares full source vector/digests.
    custody = composite_archive_custody(job=source_job, record=snapshot)
    payload = deepcopy(snapshot.snapshot_payload)
    result = {
        "selection": selection.model_dump(),
        "contract_version": "composite_review.v8",
        "snapshot_response_bytes_sha256": "sha256:" + sha256(response_bytes).hexdigest(),
        "payload": payload,
        "source_revision_vector": deepcopy(snapshot.source_revision_vector),
        "source_identity": {key: payload["selection"][key] for key in SCOPE_AXES},
        "capture_identity": {
            key: getattr(snapshot, key)
            for key in (
                "report_revision_id",
                "series_digest",
                "source_revision_digest",
                "factual_content_digest",
            )
        },
    }
    Generation.model_validate(result)
    require_equal(
        source_custody_identity(result), custody["composite_report_identity"], "V8_DERIVATION"
    )
    return result
