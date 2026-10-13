"""Malformed retained contracts cannot be promoted by wrapper rehashing."""

import json
from copy import deepcopy

import pytest

from app.composite_reporting.correction_notice.admission import require_path, validate_notice
from app.composite_reporting.correction_notice.contract import Generation, NoticeCell
from app.composite_reporting.correction_notice.selection import CorrectionNoticeSelection
from app.composite_reporting.correction_notice.source_identity import (
    CompositeDefinitionCustody,
    CompositeReportIdentityV8,
    CompositeSourceProductCustody,
    embedded_schema,
    inherited_schema,
    validate_inherited,
)
from tests.unit.composite_notice_fixture import notice_fixture, notice_job_and_capture


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://foreign/schema"},
        {"$ref": "#/$defs/missing"},
        {"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"},
    ],
)
def test_external_missing_and_recursive_schema_references_refuse(schema):
    with pytest.raises(ValueError, match="SCHEMA_REFERENCE_UNSUPPORTED"):
        embedded_schema(schema)


def test_embedded_schema_preserves_sibling_constraints_and_refuses_nonobjects(
    tmp_path, monkeypatch
):
    import app.composite_reporting.correction_notice.source_identity as source

    assert embedded_schema(
        {"$defs": {"name": {"type": "string"}}, "$ref": "#/$defs/name", "minLength": 2}
    ) == {"allOf": [{"type": "string"}, {"minLength": 2}]}
    (tmp_path / "invalid.json").write_text(json.dumps([]), encoding="utf-8")
    monkeypatch.setattr(source, "SCHEMA_ROOT", tmp_path)
    with pytest.raises(ValueError, match="SCHEMA_OBJECT_REQUIRED"):
        inherited_schema("invalid.json")


def test_inherited_identity_refuses_extra_authority_claim():
    identity = deepcopy(
        notice_fixture()["archive_path"]["nodes"][0]["document_response"][
            "composite_report_identity"
        ]
    )
    CompositeReportIdentityV8.model_validate(identity)
    identity["publication_state"] = "ATTESTED"
    with pytest.raises(ValueError, match="INHERITED_CONTRACT_INVALID"):
        validate_inherited("archive-identity.v8.schema.json", identity)


@pytest.mark.parametrize(
    "fault,code",
    [
        ("digest", "DEFINITION_DIGEST"),
        ("definition", "SOURCE_DEFINITION_CONFLICT"),
        ("membership", "SOURCE_MEMBERSHIP_CONFLICT"),
    ],
)
def test_source_custody_refuses_digest_and_definition_vector_conflicts(fault, code):
    identity = deepcopy(
        notice_fixture()["archive_path"]["nodes"][0]["document_response"][
            "composite_report_identity"
        ]
    )
    if fault == "digest":
        identity["definition"]["source_response_digest"] = "sha256:" + "0" * 64
        with pytest.raises(ValueError, match=code):
            CompositeDefinitionCustody.model_validate(identity["definition"])
        return
    if fault == "definition":
        identity["definition"]["pin"]["content_hash"] = "sha256:" + "0" * 64
    else:
        for pin in identity["source_context"]["memberships"]:
            pin["content_hash"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match=code):
        CompositeReportIdentityV8.model_validate(identity)


@pytest.mark.parametrize("fault,code", [("pair", "PAIR_INVALID"), ("path", "PATH_DUPLICATED")])
def test_order_selection_refuses_same_capture_and_duplicate_documents(fault, code):
    notice = notice_fixture()
    job, _ = notice_job_and_capture(notice)
    selection = deepcopy(job.options["composite_notice_selection"])
    CorrectionNoticeSelection.model_validate(selection)
    if fault == "pair":
        selection["after"] = selection["before"]
    else:
        selection["source_document_ids"][1] = selection["source_document_ids"][0]
    with pytest.raises(ValueError, match=code):
        CorrectionNoticeSelection.model_validate(selection)


@pytest.mark.parametrize(
    "fault,code", [("scope", "SCOPE_CONFLICT"), ("revision", "SOURCE_REVISION_CONFLICT")]
)
def test_generation_refuses_scope_and_revision_rebinding(fault, code):
    generation = deepcopy(notice_fixture()["generations"]["before"])
    Generation.model_validate(generation)
    if fault == "scope":
        generation["source_identity"]["tenant_id"] = "foreign"
    else:
        generation["capture_identity"]["report_revision_id"] = "rrv3_" + "0" * 64
    with pytest.raises(ValueError, match=code):
        Generation.model_validate(generation)


@pytest.mark.parametrize(
    "cell,code",
    [
        (
            {"canonical_value": None, "availability": "AVAILABLE", "reason_codes": []},
            "AVAILABLE_NULL",
        ),
        (
            {"canonical_value": "1", "availability": "UNAVAILABLE", "reason_codes": ["MISSING"]},
            "UNAVAILABLE_VALUE",
        ),
        (
            {"canonical_value": None, "availability": "UNAVAILABLE", "reason_codes": []},
            "UNAVAILABLE_VALUE",
        ),
        (
            {"canonical_value": None, "availability": "UNAVAILABLE", "reason_codes": ["lowercase"]},
            "GENERATED_REASON_INVALID",
        ),
    ],
)
def test_notice_cells_refuse_false_availability_or_generated_reasons(cell, code):
    with pytest.raises(ValueError, match=code):
        NoticeCell.model_validate({**cell, "source_pointer": "/notice_facts/obligations/0"})
    valid = NoticeCell.model_validate(
        {
            "canonical_value": None,
            "availability": "UNAVAILABLE",
            "reason_codes": ["MISSING"],
            "source_pointer": "/notice_facts/obligations/0",
        }
    )
    assert valid.canonical_value is None


@pytest.mark.parametrize(
    "fault,code",
    [
        ("duplicate", "PATH_DUPLICATED"),
        ("purged", "SOURCE_UNAVAILABLE"),
        ("same_generation", "ECONOMIC_PAIR_REQUIRED"),
    ],
)
def test_semantic_admission_refuses_duplicate_purged_and_same_generation_sources(fault, code):
    notice = deepcopy(notice_fixture())
    if fault == "duplicate":
        notice["archive_path"]["nodes"][1]["document_id"] = notice["archive_path"]["nodes"][0][
            "document_id"
        ]
    elif fault == "purged":
        from app.composite_reporting.correction_notice.hashing import digest, document_preimage

        node = notice["archive_path"]["nodes"][0]
        node["document_response"]["purge_status"] = "purged"
        node["document_response_digest"] = digest(document_preimage(node["document_response"]))
    else:
        notice["generations"]["after"] = deepcopy(notice["generations"]["before"])
    with pytest.raises(ValueError, match=code):
        (validate_notice if fault == "same_generation" else require_path)(notice)


def test_optional_source_product_custody_preserves_pin_and_refuses_digest_rebinding():
    from app.composite_reporting.correction_notice.admission import source_custody_identity
    from tests.unit.composite_reporting.test_source_products import load_source_packet, request_for

    pin = request_for(load_source_packet())["source_products"][0]
    product = {"pin": pin, "source_response_digest": pin["selection"]["response_digest"]}
    CompositeSourceProductCustody.model_validate(product)
    generation = deepcopy(notice_fixture()["generations"]["before"])
    generation["payload"]["source_products"] = [product]
    assert source_custody_identity(generation)["source_products"] == [product]
    product["source_response_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="SOURCE_PRODUCT_DIGEST"):
        CompositeSourceProductCustody.model_validate(product)


@pytest.mark.parametrize("fault", ["json", "untyped_option"])
def test_notice_request_refuses_wrong_output_or_untyped_selector(fault):
    from app.composite_reporting.models import CompositeReviewJobRequest

    job, _ = notice_job_and_capture(notice_fixture())
    selection = job.options["composite_notice_selection"]
    data = {"notice_selection": selection, "requested_output_formats": ["xlsx"]}
    if fault == "json":
        data["requested_output_formats"] = ["json"]
        message = "COMPOSITE_NOTICE_XLSX_REQUIRED"
    else:
        data["options"] = {"composite_notice_selection": selection}
        message = "typed notice_selection"
    with pytest.raises(ValueError, match=message):
        CompositeReviewJobRequest.model_validate(data)


@pytest.mark.asyncio
async def test_missing_notice_source_store_refuses_before_external_reads():
    from app.reporting_lineage.capture_service import (
        PortfolioReviewInputCaptureError,
        ReportingReadPortfolioReviewInputProvider,
    )

    job, _ = notice_job_and_capture(notice_fixture())
    with pytest.raises(PortfolioReviewInputCaptureError) as caught:
        await ReportingReadPortfolioReviewInputProvider().collect_for_job(job)
    assert "COMPOSITE_NOTICE_SOURCE_STORE_REQUIRED" in str(caught.value.original_error)
    assert caught.value.upstream_calls == []


@pytest.mark.asyncio
async def test_unavailable_archive_document_refuses_notice_without_partial_publication(monkeypatch):
    from unittest.mock import AsyncMock

    from app.composite_reporting.correction_notice.capture import (
        ArchiveNoticeRead,
        CorrectionNoticeInputProvider,
    )
    from app.reporting_lineage.capture_service import PortfolioReviewInputCaptureError
    from tests.unit.test_composite_notice_capture import ControlledNoticeSources

    notice = notice_fixture()
    job, _ = notice_job_and_capture(notice)
    sources = ControlledNoticeSources(notice)
    read = AsyncMock(return_value=ArchiveNoticeRead(404, {}, b"{}", "controlled-document"))
    monkeypatch.setattr(sources, "get_notice_document", read)
    with pytest.raises(PortfolioReviewInputCaptureError) as caught:
        await CorrectionNoticeInputProvider(
            ledger=sources, snapshots=sources, archive_reader=sources
        ).collect_for_job(job)
    assert "COMPOSITE_NOTICE_ARCHIVE_EVIDENCE_UNAVAILABLE" in str(caught.value.original_error)
    read.assert_awaited_once()
