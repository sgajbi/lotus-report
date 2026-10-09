"""Workbook availability needs exact runtime and template format evidence."""

from copy import deepcopy
from dataclasses import replace

import pytest

from app.report_ordering_catalogue.definitions import REPORT_FAMILY_DEFINITIONS
from app.report_ordering_catalogue.service import ReportOrderingCatalogueService
from tests.unit.report_ordering_catalogue.test_report_ordering_catalogue_service import (
    _ready_render_metadata,
    _RenderMetadataClient,
)


def workbook_sources():
    definition = next(d for d in REPORT_FAMILY_DEFINITIONS if d.report_type == "composite_review")
    definition = replace(definition, supported_output_formats=("json", "xlsx"))
    metadata = _ready_render_metadata()
    metadata["supportedOutputFormats"] = ["pdf", "xlsx"]
    projection = {
        "templates": [
            {
                "template_id": "composite-review",
                "template_version": "v1",
                "status": "active",
                "template_publication": "development",
                "supported_report_types": ["composite_review"],
                "supported_report_data_contract_versions": ["composite_review.v1"],
                "supported_output_formats": ["xlsx"],
            }
        ]
    }
    return definition, metadata, projection


@pytest.mark.asyncio
@pytest.mark.parametrize("formats", [None, ["pdf"], "xlsx", ["xlsx", 42]])
async def test_workbook_refuses_missing_or_incompatible_template_format(formats):
    definition, metadata, projection = workbook_sources()
    entry = projection["templates"][0]
    if formats is None:
        entry.pop("supported_output_formats")
    else:
        entry["supported_output_formats"] = formats
    service = ReportOrderingCatalogueService(
        render_client=_RenderMetadataClient(200, metadata, templates_payload=projection),
        definitions=[definition],
    )
    family = (await service.get_catalogue()).report_families[0]
    assert family.supportability.state == "partial"
    assert family.output_formats[0].state == "ready"
    assert family.output_formats[1].format_id == "xlsx"
    assert family.output_formats[1].state == "unavailable"
    assert family.output_formats[1].reason_code == "template_output_format_not_supported"


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_state", ["ready", "degraded"])
async def test_pdf_runtime_readiness_cannot_establish_workbook_support(runtime_state):
    definition, metadata, projection = workbook_sources()
    metadata["supportedOutputFormats"] = ["pdf"]
    metadata["supportability"]["state"] = runtime_state
    family = (
        await ReportOrderingCatalogueService(
            render_client=_RenderMetadataClient(200, metadata, templates_payload=projection),
            definitions=[definition],
        ).get_catalogue()
    ).report_families[0]
    assert family.output_formats[1].state == "unavailable"
    assert family.output_formats[1].reason_code == "render_output_format_not_supported"


@pytest.mark.asyncio
async def test_exact_development_workbook_is_orderable_for_internal_use():
    definition, metadata, projection = workbook_sources()
    original = deepcopy(projection)
    family = (
        await ReportOrderingCatalogueService(
            render_client=_RenderMetadataClient(200, metadata, templates_payload=projection),
            definitions=[definition],
        ).get_catalogue()
    ).report_families[0]
    assert family.client_release_posture == "internal_control_only"
    assert family.output_formats[1].format_id == "xlsx"
    assert family.output_formats[1].state == "ready"
    assert family.supportability.state == "ready"
    assert projection == original
