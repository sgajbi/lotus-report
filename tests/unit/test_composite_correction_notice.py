import hashlib
import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from app.composite_reporting.correction_notice.contract import CorrectionNoticeData
from app.composite_reporting.correction_notice.custody import notice_archive_custody
from app.composite_reporting.correction_notice.hashing import (
    digest,
    document_preimage,
    evidence_preimage,
    manifest_preimage,
    pair_preimage,
)
from app.composite_reporting.correction_notice.selection import SelectionTuple
from app.composite_reporting.correction_notice.source_capture import generation_from_capture
from app.composite_reporting.correction_notice.source_identity import inherited_schema
from app.composite_reporting.eligibility_tables import preflight_eligibility_package
from app.composite_reporting.render_package import build_composite_render_package
from tests.unit.composite_notice_fixture import (
    notice_fixture,
    notice_job_and_capture,
    retained_source_job_and_capture,
)


def test_retained_complete_notice_and_native_schema():
    notice = notice_fixture()
    assert len(notice["tables"]) == 32
    assert len(notice["pair_index"]) == 1289
    schema = CorrectionNoticeData.model_json_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(notice)
    assert notice["notice_completeness"] == "PARTIAL"
    assert notice["publication_state"] == "NOT_ATTESTED"


def test_accepted_native_archive_relationship_schema_and_wire_known_answer():
    schema = inherited_schema("archive-relationship.v1.schema.json")
    record = notice_fixture()["archive_path"]["edges"][1]["acknowledgement"]["raw_payload"]
    assert len(schema["required"]) == 8
    assert schema["additionalProperties"] is False
    assert "current_document_id" not in schema["properties"]
    Draft202012Validator(schema).validate(record)
    wire = json.dumps(
        {key: record[key] for key in schema["required"]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(wire) == 423
    assert hashlib.sha256(wire).hexdigest() == (
        "b4287a71686f99e8bcf4f08e881c4b2986b993ac9d51fb5092ee43b4131ccf1a"
    )
    for fault in ("extra", "omitted"):
        invalid = deepcopy(record)
        if fault == "extra":
            invalid["current_document_id"] = record["target_document_id"]
        else:
            invalid.pop("requested_by")
        assert not Draft202012Validator(schema).is_valid(invalid)


def test_native_notice_custody_and_full_package_capacity():
    notice = notice_fixture()
    job, record = notice_job_and_capture(notice)
    package = build_composite_render_package(
        job=job,
        snapshot=notice,
        render_job_id="rdr_notice_test",
        snapshot_id=record.snapshot_id,
        report_revision_id=record.report_revision_id,
        snapshot_record=record,
    )
    totals = preflight_eligibility_package(package, failure_prefix="COMPOSITE_NOTICE")
    assert 32 < totals["sheets"] <= 64
    assert package["report_data"] == notice
    custody = package["render_context"]["archive"]["composite_report_identity"]
    assert (
        custody["generations"]["before"]["source_document_id"]
        == notice["archive_path"]["nodes"][0]["document_id"]
    )
    assert record.report_revision_id not in [
        generation["selection"]["report_revision_id"]
        for generation in notice["generations"].values()
    ]


@pytest.mark.parametrize("side", ["before", "after"])
def test_native_v8_capture_derivation_and_authorization(side):
    notice = notice_fixture()
    expected = notice["generations"][side]
    job, record, raw = retained_source_job_and_capture(side, notice)
    arguments = dict(
        selection=SelectionTuple.model_validate(expected["selection"]),
        source_job=job,
        snapshot=record,
        response_bytes=raw,
        tenant_id=job.tenant_id,
        region=job.region,
    )
    assert generation_from_capture(**arguments) == expected
    arguments["tenant_id"] = "different-tenant"
    with pytest.raises(ValueError, match="SOURCE_ACCESS_REFUSED"):
        generation_from_capture(**arguments)


@pytest.mark.parametrize(
    "mutation", ["omitted_coordinate", "promoted_obligation", "changed_source"]
)
def test_rehashed_wrapper_cannot_authorize_semantic_mutation(mutation):
    notice = deepcopy(notice_fixture())
    if mutation == "omitted_coordinate":
        notice["pair_index"].pop()
    elif mutation == "promoted_obligation":
        notice["notice_completeness"] = "COMPLETE"
    else:
        notice["generations"]["before"]["payload"]["tables"][0]["rows"][0]["cells"]["return"][
            "canonical_value"
        ] = "99"
    notice["manifest_digest"] = digest(manifest_preimage(notice))
    with pytest.raises(ValueError):
        CorrectionNoticeData.model_validate(notice)


@pytest.mark.parametrize(
    "fault",
    [
        "path_role",
        "foreign_document",
        "ack_target",
        "null_substitution",
        "changed_policy",
        "duplicate_coordinate",
    ],
)
def test_independent_semantics_refuse_resealed_fault(fault):
    notice = notice_fixture()
    if fault == "path_role":
        notice["archive_path"]["edges"][0]["semantic_role"] = "ECONOMIC_CORRECTION"
    elif fault == "foreign_document":
        notice["archive_path"]["nodes"][0]["document_response"]["tenant_id"] = "foreign"
    elif fault == "ack_target":
        notice["archive_path"]["edges"][1]["acknowledgement"]["raw_payload"][
            "target_document_id"
        ] = "unknown"
    elif fault == "null_substitution":
        cells = notice["tables"][0]["rows"][0]["cells"]
        cells["authority_receipt"]["canonical_value"] = "0"
    elif fault == "changed_policy":
        notice["tables"][0]["columns"][0]["label"] = "Changed source policy"
    else:
        notice["pair_index"][-1] = deepcopy(notice["pair_index"][0])
    for node in notice["archive_path"]["nodes"]:
        node["document_response_digest"] = digest(document_preimage(node["document_response"]))
    for edge in notice["archive_path"]["edges"]:
        ack = edge["acknowledgement"]
        ack["digest"] = digest(evidence_preimage(ack["kind"], ack["raw_payload"]))
    notice["pair_fingerprint"] = digest(pair_preimage(notice))
    notice["manifest_digest"] = digest(manifest_preimage(notice))
    with pytest.raises(ValueError):
        CorrectionNoticeData.model_validate(notice)


@pytest.mark.parametrize(
    "field,value",
    [
        ("snapshot_hash", "sha256:" + "0" * 64),
        ("report_data_contract_version", "composite_review.v8"),
        ("report_type", "portfolio_review"),
        ("report_revision_id", "rrv3_" + "0" * 64),
    ],
)
def test_own_notice_capture_must_match_native_identity(field, value):
    notice = notice_fixture()
    job, capture = notice_job_and_capture(notice)
    bad = capture.model_copy(update={field: value})
    with pytest.raises(ValueError, match="COMPOSITE_NOTICE_OWN_"):
        notice_archive_custody(job=job, record=bad)


def test_custody_cannot_substitute_a_different_accepted_order():
    notice = notice_fixture()
    job, capture = notice_job_and_capture(notice)
    options = deepcopy(job.options)
    selection = options["composite_notice_selection"]
    selection["before"], selection["after"] = selection["after"], selection["before"]
    with pytest.raises(ValueError, match="COMPOSITE_NOTICE_OWN_ORDER_SELECTION"):
        notice_archive_custody(job=job.model_copy(update={"options": options}), record=capture)
