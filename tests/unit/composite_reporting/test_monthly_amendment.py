"""Source correction admission and historical selector compatibility."""

import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.composite_reporting.admission import CompositeEvidenceRefused
from app.composite_reporting.amendment_admission import require_link, require_projected_decisions
from app.composite_reporting.amendment_contract import (
    CompositeAmendmentReportData,
    composite_amendment_report_schema,
)
from app.composite_reporting.amendment_tables import build_amendment_dataset
from app.composite_reporting.models import CompositeReviewJobRequest
from app.composite_reporting.render_package import build_composite_render_package
from app.config import settings
from app.reporting_jobs.ledger import compute_request_hash
from app.reporting_jobs.models import ReportCallerContext
from app.reporting_lineage.capture_service import _hash_payload
from tests.unit.composite_reporting.amendment_examples import example
from tests.unit.composite_reporting.test_eligibility_capture import eligibility_job
from tests.unit.composite_reporting.test_eligibility_contract import evaluated_example
from tests.unit.composite_reporting.test_eligibility_history import published_example
from tests.unit.composite_reporting.test_registered_lifecycle import (
    HEADERS,
    app,
    composite_lifecycle,
)


@pytest.mark.parametrize("definition_version", ["v1", "v2"])
@pytest.mark.parametrize("revision", [2, 3])
@pytest.mark.parametrize("evaluated", [False, True])
def test_exact_versioned_source_correction(definition_version, revision, evaluated):
    selection, months = example(definition_version, revision, evaluated)
    dataset = build_amendment_dataset(selection, months)
    assert dataset["contract_version"] == "composite_review.v6"
    assert dataset["source_months"] == months
    assert len(dataset["source_months"][0]["lineage_receipts"]) == revision - 1
    assert len(dataset["tables"]) == 9
    assert all(table["rows"] for table in dataset["tables"])
    request = CompositeReviewJobRequest.model_validate(
        {"eligibility_selection": selection.model_dump(mode="json")}
    )
    assert request.eligibility_selection == selection
    assert request.capture_options()["composite_eligibility_selection"] == selection.model_dump(
        mode="json"
    )


def test_historical_selector_wire_is_exact():
    selection, _ = evaluated_example()
    original = selection.model_dump(mode="json")
    request = CompositeReviewJobRequest.model_validate({"eligibility_selection": original})
    assert request.eligibility_selection.model_dump(mode="json") == original
    assert "selection_version" not in request.model_dump(mode="json")["eligibility_selection"]


@pytest.mark.parametrize(
    "factory,expected",
    [
        (evaluated_example, "ad3ff113995dd9e1d1e18c5e0563d0f2295ee13fd7d154861607b58d0607e62c"),
        (published_example, "82db808e25ceccd092291f181869f01d0fc49734775b7465ce0a2894ff08f4c8"),
    ],
)
def test_frozen_v4_request_fingerprints(factory, expected):
    selection, _ = factory()
    request = CompositeReviewJobRequest(eligibility_selection=selection)
    caller = ReportCallerContext(
        triggered_by="reader",
        caller_application="lotus-gateway",
        tenant_id=selection.tenant_id,
        region="APAC",
        correlation_id="fixed-correlation",
        trace_id="fixed-trace",
    )
    assert (
        compute_request_hash(report_type="composite_review", request=request, caller_context=caller)
        == expected
    )


def test_v6_xlsx_package_requires_persisted_snapshot():
    selection, _ = example()
    job = eligibility_job(selection).model_copy(update={"requested_output_formats": ["xlsx"]})
    with pytest.raises(ValueError, match="COMPOSITE_RENDER_PERSISTED_SNAPSHOT_REQUIRED"):
        build_composite_render_package(
            job=job,
            snapshot={"contract_version": "composite_review.v6"},
            render_job_id="blocked",
            snapshot_id="blocked",
            report_revision_id=None,
            snapshot_record=None,
        )


@pytest.mark.parametrize("field", ["selection_version", "lineage_receipts"])
def test_variant_cannot_be_implicitly_downgraded(field):
    selection, _ = example()
    wire = selection.model_dump(mode="json")
    if field == "selection_version":
        wire.pop(field)
    else:
        wire["months"][0].pop(field)
    with pytest.raises(ValidationError):
        CompositeReviewJobRequest.model_validate({"eligibility_selection": wire})


def test_missing_and_oversized_lineage_refused_before_capture():
    selection, _ = example()
    wire = selection.model_dump(mode="json")
    for receipts in ([], wire["months"][0]["lineage_receipts"] * 16):
        changed = deepcopy(wire)
        changed["months"][0]["lineage_receipts"] = receipts
        with pytest.raises(ValidationError):
            CompositeReviewJobRequest.model_validate({"eligibility_selection": changed})


def test_response_digest_conflict_is_not_admitted():
    selection, months = example()
    months[0]["receipt"]["lineage"]["reason"] = "tampered"
    with pytest.raises((CompositeEvidenceRefused, ValidationError)):
        build_amendment_dataset(selection, months)


@pytest.mark.parametrize(
    "field",
    [
        "predecessor_approval_binding",
        "predecessor_receipt_binding",
        "original_approval_binding",
        "expected_authority_binding",
        "projection_parent_membership_binding",
    ],
)
@pytest.mark.parametrize("axis", ["product_name", "product_version", "revision", "digest"])
def test_every_lineage_binding_axis_is_exact(field, axis):
    _, months = example()
    proposal = deepcopy(months[0]["receipt"]["approval"]["proposal"])
    claim = proposal["amendment"][field]
    claim[axis] = "sha256:" + "0" * 64 if axis == "digest" else "wrong"
    with pytest.raises((CompositeEvidenceRefused, ValidationError)):
        require_link(proposal, months[0]["lineage_receipts"][0], months[0]["lineage_receipts"][-1])


@pytest.mark.parametrize(
    "change,code",
    [
        ("policy", "POLICY_CHANGE_UNSUPPORTED"),
        ("population", "POPULATION_CHANGE_UNSUPPORTED"),
        ("source", "SOURCE_UNCHANGED"),
        ("clock", "CLOCK_CONFLICT"),
        ("reason", "REASON_REQUIRED"),
        ("evidence", "AMBIGUOUS_EVIDENCE"),
        ("window", "WINDOW_CONFLICT"),
        ("revision", "REVISION_REUSED"),
        ("cascade", "CASCADE_UNSUPPORTED"),
    ],
)
def test_same_policy_population_ordinary_month_only(change, code):
    _, months = example()
    receipt = months[0]["receipt"]
    prior = months[0]["lineage_receipts"][0]
    root = months[0]["lineage_receipts"][-1]
    proposal = deepcopy(receipt["approval"]["proposal"])
    if change == "policy":
        proposal["policy_approval"]["approved_by"] = "changed-checker"
    elif change == "population":
        proposal["universe"]["expected_portfolio_ids"] = ["other"]
    elif change == "source":
        proposal["observations"] = deepcopy(prior["approval"]["proposal"]["observations"])
    elif change == "clock":
        proposal["proposed_at"] = "2000-01-01T00:00:00Z"
    elif change == "reason":
        proposal["amendment"]["reason"] = " "
    elif change == "evidence":
        proposal["amendment"]["evidence_bindings"] *= 2
    elif change == "window":
        proposal["amendment"]["affected_to"] = "2026-10-01"
    elif change == "cascade":
        proposal["amendment"]["expected_current_publication_sequence"] += 1
    else:
        proposal["evaluation_revision"] = prior["approval"]["proposal"]["evaluation_revision"]
    with pytest.raises(CompositeEvidenceRefused, match=code):
        require_link(proposal, prior, root)


def test_cyclic_retained_chain_refused_even_with_matching_caller_pins():
    selection, months = example()
    month = months[0]
    month["lineage_receipts"][1] = deepcopy(month["lineage_receipts"][0])
    pin = selection.model_dump(mode="json")
    pin["months"][0]["lineage_receipts"][1] = deepcopy(pin["months"][0]["lineage_receipts"][0])
    changed = type(selection).model_validate(pin)
    with pytest.raises((CompositeEvidenceRefused, ValidationError), match="LINEAGE_CYCLE"):
        build_amendment_dataset(changed, months)


def test_schema_and_executable_expected_outputs_are_exact():
    root = next(
        parent for parent in Path(__file__).resolve().parents if (parent / "contracts").is_dir()
    )
    schema = json.loads((root / "contracts/composite_review.v6.schema.json").read_text())
    assert schema == composite_amendment_report_schema()
    for version in ("v1", "v2"):
        for evaluated in (False, True):
            kind = "evaluated" if evaluated else "published"
            path = (
                root
                / "contracts/examples"
                / f"composite-review.v6.definition-{version}.{kind}.expected.json"
            )
            expected = json.loads(path.read_text())
            Draft202012Validator(schema).validate(expected)
            selection, months = example(version, evaluated=evaluated)
            assert expected == build_amendment_dataset(selection, months)


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("evaluated", [False, True])
def test_retained_json_object_key_order_does_not_change_projection(version, evaluated):
    selection, months = example(version, evaluated=evaluated)
    captured = build_amendment_dataset(selection, months)
    reordered = json.loads(json.dumps(captured, sort_keys=True))
    assert (
        CompositeAmendmentReportData.model_validate(reordered).model_dump(mode="json") == captured
    )
    reordered_source = json.loads(json.dumps(months, sort_keys=True))
    assert build_amendment_dataset(selection, reordered_source) == captured


async def exercise_amendment_lifecycle(
    tmp_path, monkeypatch, version, *, adapters=None, corrupt=False
):
    """Named controlled Manage transport; registered native Report adapters/worker."""
    monkeypatch.setattr(settings, "manage_read_actor_id", "configured-reader")
    monkeypatch.setattr(settings, "manage_read_service_identity", "configured-report-service")
    state, calls, retained = {}, [], []

    async def source(**kwargs):
        calls.append(deepcopy(kwargs))
        month = state["month"]
        binding = kwargs["json_body"]
        if binding is not None:
            receipts = [month["receipt"], *month["lineage_receipts"]]
            response = next(
                item for item in receipts if item["approval"]["content_hash"] == binding["digest"]
            )
            assert binding["product_version"] == response["approval"]["product_version"]
            assert binding["revision"] == response["approval"]["proposal"]["evaluation_revision"]
            assert kwargs["url"].endswith("/eligibility-evidence/resolve")
        elif "/publications/" in kwargs["url"]:
            sequence = int(kwargs["url"].rsplit("/", 1)[1])
            response = next(
                item
                for item in (month["publication"], month["parent_publication"])
                if item["sequence"] == sequence
            )
        elif "/universe-attestations/" in kwargs["url"]:
            response = month["universe"]
        else:
            revision = kwargs["url"].rsplit("/", 1)[1]
            response = next(
                item
                for item in (month["membership"], month["parent_membership"])
                if item["membership_revision"] == revision
            )
        wire = deepcopy(response)
        if (
            corrupt
            and binding is not None
            and wire["content_hash"] == month["receipt"]["content_hash"]
        ):
            wire["lineage"]["reason"] = "source changed after the exact caller pin"
        return 200, wire

    monkeypatch.setattr("app.clients.manage_client.bounded_read_with_retry", source)
    with composite_lifecycle(tmp_path, monkeypatch, adapters=adapters) as (
        ledger,
        store,
        worker,
        performance,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://report"
        ) as client:
            for revision in (2, 3):
                selection, months = example(version, revision)
                state["month"] = months[0]
                headers = {
                    **HEADERS,
                    "X-Tenant-Id": selection.tenant_id,
                    "Idempotency-Key": uuid4().hex,
                }
                request = {"eligibility_selection": selection.model_dump(mode="json")}
                before = len(calls)
                unavailable = await client.post(
                    "/reports/composite-reviews",
                    headers=headers,
                    json={**request, "requested_output_formats": ["xlsx"]},
                )
                assert unavailable.status_code == 503 and len(calls) == before
                ordered = await client.post(
                    "/reports/composite-reviews", headers=headers, json=request
                )
                assert ordered.status_code == 202, ordered.text
                job_id = ordered.json()["report_job_id"]
                assert (
                    ledger.get_job(job_id).accepted_document_contract[
                        "input_snapshot_contract_version"
                    ]
                    == "composite_review.v6"
                )
                done = await worker.run_once(
                    worker_id="amendment-json", max_items=1, lease_seconds=30
                )
                if corrupt:
                    assert ledger.get_job(job_id).status == "failed"
                    snapshot = store.get_snapshot_by_job(job_id)
                    assert snapshot.snapshot_payload["capture_status"] == "failed"
                    assert snapshot.report_revision_id is None
                    upstream = store.list_upstream_calls(snapshot.snapshot_id)
                    refused = deepcopy(months[0]["receipt"])
                    refused["lineage"]["reason"] = "source changed after the exact caller pin"
                    assert any(row.response_hash == _hash_payload(refused) for row in upstream)
                    assert any(row.supportability_status == "error" for row in upstream)
                    assert performance["calls"] == []
                    return [(job_id, snapshot.model_dump(mode="json"))]
                assert done.completed_count == 1
                assert ledger.get_job(job_id).status == "data_ready", ledger.get_job(
                    job_id
                ).failure_message
                snapshot = store.get_snapshot_by_job(job_id)
                assert snapshot.snapshot_payload == build_amendment_dataset(selection, months)
                revisions = snapshot.source_revision_vector["revisions"]
                assert {
                    (row["source_snapshot_id"], row["content_hash"], row["source_product_version"])
                    for row in revisions
                } == {
                    (
                        item["approval"]["proposal"]["evaluation_revision"],
                        item["content_hash"],
                        item["product_version"],
                    )
                    for item in [months[0]["receipt"], *months[0]["lineage_receipts"]]
                }
                assert len(calls) - before == 6 + len(selection.months[0].lineage_receipts)
                retry = await client.post(
                    "/reports/composite-reviews", headers=headers, json=request
                )
                assert retry.json()["report_job_id"] == job_id
                read = await client.get(f"/reports/jobs/{job_id}/snapshot", headers=headers)
                assert (
                    read.status_code == 200
                    and read.json()["snapshot_hash"] == snapshot.snapshot_hash
                )
                foreign = await client.get(
                    f"/reports/jobs/{job_id}/snapshot",
                    headers={**headers, "X-Tenant-Id": "foreign"},
                )
                assert foreign.status_code == 404
                retained.append((job_id, snapshot.model_dump(mode="json")))
            assert performance["calls"] == []
            assert all(item["headers"]["X-Capabilities"] == "manage.read" for item in calls)
            assert not any("Authorization" in item["headers"] for item in calls)
            # Changed supplier state cannot mutate the earlier captured revision.
            first = store.get_snapshot_by_job(retained[0][0])
            assert first.model_dump(mode="json") == retained[0][1]
            assert first.report_revision_id != retained[1][1]["report_revision_id"]
    return retained


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["v1", "v2"])
async def test_registered_amendment_json_lifecycle(tmp_path, monkeypatch, version):
    await exercise_amendment_lifecycle(tmp_path, monkeypatch, version)


@pytest.mark.asyncio
async def test_registered_worker_retains_source_refusal(tmp_path, monkeypatch):
    await exercise_amendment_lifecycle(tmp_path, monkeypatch, "v2", corrupt=True)


@pytest.mark.parametrize(
    "field",
    [
        "reason_code",
        "status",
        "discretionary",
        "approval_ref",
        "source_snapshot_id",
        "effective_from",
    ],
)
def test_projected_decisions_match_source_evidence(field):
    _, months = example(revision=2)
    month = months[0]
    proposal = month["receipt"]["approval"]["proposal"]
    current = next(
        row
        for row in month["membership"]["decisions"]
        if row["effective_from"] == proposal["amendment"]["affected_from"]
    )
    current[field] = False if field == "discretionary" else "tampered"
    with pytest.raises(CompositeEvidenceRefused, match="PROJECTED_DECISION_CONFLICT"):
        require_projected_decisions(month, proposal)
