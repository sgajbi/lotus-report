"""Registered Report API/SQLite capture with controlled source ports, not live restore."""

from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from app.composite_reporting.correction_notice.capture import native_snapshot_read_bytes
from app.report_ordering_catalogue.service import ReportOrderingCatalogueService
from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.replay_policy import composite_replay_request, resolution_replay_supported
from app.reporting_lineage.capture_service import (
    PortfolioReviewSnapshotCaptureService,
    ReportingReadPortfolioReviewInputProvider,
)
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render.package_builder import _build_render_package
from app.reporting_render.replay_service import (
    PortfolioReviewReplayService,
    get_portfolio_review_replay_service,
)
from tests.unit.composite_notice_fixture import notice_fixture, notice_job_and_capture
from tests.unit.composite_reporting.test_registered_lifecycle import (
    HEADERS,
    app,
    composite_lifecycle,
)
from tests.unit.test_composite_notice_capture import ControlledNoticeSources


@pytest.mark.asyncio
async def test_registered_order_capture_read_idempotency_reopen_and_source_off_package(
    tmp_path, monkeypatch
):
    async def support(self, **kwargs):
        assert kwargs["template_version"] == "v9"
        return SimpleNamespace(state="ready")

    monkeypatch.setattr(ReportOrderingCatalogueService, "document_contract_supportability", support)
    fixture = notice_fixture()
    controlled_job, _ = notice_job_and_capture(fixture)
    request = {
        "notice_selection": controlled_job.options["composite_notice_selection"],
        "requested_output_formats": ["xlsx"],
    }
    headers = {
        **HEADERS,
        "X-Tenant-Id": controlled_job.tenant_id,
        "Idempotency-Key": "native-notice-order",
    }
    sources = ControlledNoticeSources(fixture)
    with composite_lifecycle(tmp_path, monkeypatch) as (ledger, store, _, performance):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            accepted = await client.post(
                "/reports/composite-reviews", json=request, headers=headers
            )
            assert accepted.status_code == 202, accepted.text
            job_id = accepted.json()["report_job_id"]
            again = await client.post("/reports/composite-reviews", json=request, headers=headers)
            assert again.status_code == 202
            assert again.json()["report_job_id"] == job_id
            altered = deepcopy(request)
            altered["notice_selection"]["before"]["snapshot_hash"] = "sha256:" + "0" * 64
            conflict = await client.post(
                "/reports/composite-reviews", json=altered, headers=headers
            )
            assert conflict.status_code == 409
            assert sources.calls == []
            provider = ReportingReadPortfolioReviewInputProvider(
                job_ledger=sources,
                snapshot_store=sources,
                notice_archive_reader=sources,
            )
            captured = await PortfolioReviewSnapshotCaptureService(
                snapshot_store=store,
                job_ledger=ledger,
                portfolio_review_input_provider=provider,
            ).capture_for_job(ledger.get_job(job_id))
            assert captured.status == "data_ready", captured.failure_message
            snapshot = store.get_snapshot_by_job(job_id)
            response = await client.get(f"/reports/jobs/{job_id}/snapshot", headers=headers)
            assert response.status_code == 200
            assert response.content == native_snapshot_read_bytes(snapshot)
            assert response.json()["report_data_contract_version"] == "composite_review.v9"
            foreign = await client.get(
                f"/reports/jobs/{job_id}/snapshot", headers={**headers, "X-Tenant-Id": "foreign"}
            )
            assert foreign.status_code == 404
            before_calls = deepcopy(sources.calls)
            reopened_ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
            reopened_store = ReportInputSnapshotStore(tmp_path / "snapshots.sqlite3")
            reopened_job = reopened_ledger.get_job(job_id)
            retained = reopened_store.get_snapshot_by_job(job_id)
            assert retained.snapshot_hash == snapshot.snapshot_hash
            assert resolution_replay_supported(reopened_job)
            assert composite_replay_request(reopened_job).capture_options() == reopened_job.options
            package = _build_render_package(
                job=reopened_job,
                snapshot=retained.snapshot_payload,
                render_job_id="rdr_controlled_notice",
                snapshot_id=retained.snapshot_id,
                report_revision_id=retained.report_revision_id,
                snapshot_record=retained,
            )
            assert package["report_data"] == retained.snapshot_payload
            assert sources.calls == before_calls
            assert performance["calls"] == []

            # Synthetic lifecycle setup uses only native ledger transitions;
            # no artifact, remote Archive record or restored source DB is claimed.
            transition = dict(
                job_id=job_id,
                actor="actor-a",
                correlation_id="notice-proof",
                trace_id="0123456789abcdef0123456789abcdef",
            )
            reopened_ledger.mark_rendering(
                **transition,
                render_job_id="rdr_controlled_notice",
                output_format="xlsx",
                template_id="composite-review",
                template_version="v9",
            )
            reopened_ledger.mark_completed(
                **transition,
                render_job_id="rdr_controlled_notice",
                output_format="xlsx",
                template_id="composite-review",
                template_version="v9",
                template_publication="development",
                artifact_sha256="sha256:" + "1" * 64,
                bounded_determinism_fingerprint=None,
                runtime_engine="controlled-test",
                runtime_engine_version="test",
                render_duration_ms=1,
            )
            reopened_ledger.mark_archiving(
                **transition, archive_request_id="areq_controlled_notice"
            )
            reopened_ledger.mark_failed(
                **transition,
                failure_category="archive_handoff_failed",
                failure_message="Controlled absent archive",
                retry_eligible=True,
            )

            class SourcesOff:
                async def capture_for_job(self, job):
                    pytest.fail("Retained notice replay must not recollect any source")

            class ConfirmedAbsent:
                async def get_document_by_request_id(self, archive_request_id, **kwargs):
                    assert archive_request_id == "areq_controlled_notice"
                    return 404, {"error": {"code": "document_not_found"}}

            class ControlledRender:
                async def render_for_job(self, job):
                    replay_capture = reopened_store.get_snapshot_by_job(job.job_id)
                    _build_render_package(
                        job=job,
                        snapshot=replay_capture.snapshot_payload,
                        render_job_id="rdr_replayed_control",
                        snapshot_id=replay_capture.snapshot_id,
                        report_revision_id=replay_capture.report_revision_id,
                        snapshot_record=replay_capture,
                    )
                    return job

            replay_service = PortfolioReviewReplayService(
                ledger=reopened_ledger,
                capture_service=SourcesOff(),
                render_service=ControlledRender(),
                snapshot_store=reopened_store,
                archive_resolver=ConfirmedAbsent(),
            )
            app.dependency_overrides[get_portfolio_review_replay_service] = lambda: replay_service
            replay_headers = {**headers, "Idempotency-Key": "notice-retained-replay"}
            replay = await client.post(
                f"/reports/jobs/{job_id}/replay",
                headers=replay_headers,
                json={"reason": "Controlled source-off recovery"},
            )
            assert replay.status_code == 202, replay.text
            replay_id = replay.json()["replayed_report_job_id"]
            retry = await client.post(
                f"/reports/jobs/{job_id}/replay",
                headers=replay_headers,
                json={"reason": "Controlled source-off recovery"},
            )
            assert retry.status_code == 202, retry.text
            assert retry.json()["replayed_report_job_id"] == replay_id
            replay_snapshot = reopened_store.get_snapshot_by_job(replay_id)
            assert replay_snapshot.snapshot_payload == retained.snapshot_payload
            assert replay_snapshot.report_revision_id == retained.report_revision_id
            assert sources.calls == before_calls
