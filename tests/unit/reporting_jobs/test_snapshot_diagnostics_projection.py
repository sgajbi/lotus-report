"""Diagnostics readback translates legacy lifecycle vocabulary (#315 P2).

Policy 1.0.0 rows store "rerender_from_snapshot"; the governed contract says
they READ AS the 1.1.0 capability claim without the stored bytes changing -
so the projection, the one surface operators see, must present
"snapshot_recomposition" while rerender_available stays a separately
derived command fact.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_lineage.models import ReportInputSnapshotRecord
from app.routers.report_jobs import _snapshot_to_diagnostics


def _legacy_snapshot() -> ReportInputSnapshotRecord:
    return ReportInputSnapshotRecord.model_construct(
        snapshot_id="rsnap_legacy",
        report_job_id="rjob_legacy",
        snapshot_hash="sha256:abc",
        supportability_status="complete",
        completeness_status="complete",
        captured_at=datetime.now(UTC),
        report_revision_id="rrv2_legacy",
        series_digest=None,
        source_revision_digest=None,
        factual_content_digest=None,
        factual_boundary_version=None,
        source_revision_vector=None,
        source_cut_coherence=None,
        lifecycle={
            "policy_ref": "report-input-snapshot-standard",
            "policy_version": "1.0.0",
            "reproduction_availability": "rerender_from_snapshot",
            "lifecycle_authority": "report-input-snapshot",
        },
    )


def _unarchived_record() -> ReportJobLedgerRecord:
    return ReportJobLedgerRecord.model_construct(
        status="failed",
        requested_output_formats=["json"],
        render_job_id=None,
        archive_document_id=None,
    )


def test_a_policy_1_0_0_row_reads_as_the_capability_claim() -> None:
    diagnostics = _snapshot_to_diagnostics(_legacy_snapshot(), _unarchived_record())

    assert diagnostics.reproduction_availability == "snapshot_recomposition"
    assert diagnostics.lifecycle_policy_ref == "report-input-snapshot-standard"
    # The command fact stays independently derived - a failed JSON-only job
    # advertises no rerender path even while the snapshot capability stands.
    assert diagnostics.rerender_available is False
