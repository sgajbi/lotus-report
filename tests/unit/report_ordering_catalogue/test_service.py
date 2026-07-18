from dataclasses import replace

import pytest

from app.report_ordering_catalogue.definitions import REPORT_FAMILY_DEFINITIONS
from app.report_ordering_catalogue.service import (
    ReportCatalogueDefinitionError,
    ReportOrderingCatalogueService,
)


class _RenderMetadataClient:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self.payload = payload
        self.calls: list[tuple[str | None, str | None]] = []

    async def get_metadata(self, correlation_id=None, trace_id=None):
        self.calls.append((correlation_id, trace_id))
        return self.status_code, self.payload


def _ready_render_metadata() -> dict[str, object]:
    return {
        "supportedOutputFormats": ["pdf"],
        "supportability": {
            "state": "ready",
            "reason": "render_supportability_ready",
            "deterministicOutputSupported": True,
            "templateRegistryReady": True,
            "runtimeAvailable": True,
        },
    }


@pytest.mark.asyncio
async def test_catalogue_maps_business_configuration_from_ready_sources() -> None:
    render_client = _RenderMetadataClient(200, _ready_render_metadata())
    service = ReportOrderingCatalogueService(render_client=render_client)

    response = await service.get_catalogue(
        correlation_id="corr-catalogue",
        trace_id="trace-catalogue",
    )

    assert response.contract_version == "report-ordering-catalogue.v1"
    assert response.supportability.state == "ready"
    assert render_client.calls == [("corr-catalogue", "trace-catalogue")]
    assert [family.report_family_id for family in response.report_families] == [
        "portfolio_review",
        "proof_pack",
        "rebalance_wave",
        "outcome_review",
    ]
    portfolio_review = response.report_families[0]
    assert portfolio_review.business_label == "Portfolio review report"
    assert portfolio_review.client_release_posture == (
        "advisor_review_required_distribution_not_supported"
    )
    assert [section.business_label for section in portfolio_review.sections[:3]] == [
        "Client and mandate profile",
        "Portfolio overview",
        "Allocation and portfolio construction",
    ]
    assert {field.field_id for field in portfolio_review.configuration_fields} == {
        "as_of_date",
        "reporting_currency",
        "benchmark_code",
        "allocation_dimensions",
    }
    assert {output.format_id: output.state for output in portfolio_review.output_formats} == {
        "json": "ready",
        "pdf": "ready",
    }
    payload = response.model_dump()
    assert "template_id" not in str(payload)
    assert "options" not in payload["report_families"][0]


@pytest.mark.asyncio
async def test_catalogue_keeps_structured_data_ready_when_render_is_unavailable() -> None:
    service = ReportOrderingCatalogueService(
        render_client=_RenderMetadataClient(503, {"detail": "render unavailable"})
    )

    response = await service.get_catalogue()

    assert response.supportability.state == "partial"
    for family in response.report_families:
        outputs = {output.format_id: output for output in family.output_formats}
        assert outputs["json"].state == "ready"
        assert outputs["pdf"].state == "unavailable"
        assert outputs["pdf"].reason_code == "render_metadata_unavailable"
        assert family.supportability.state == "partial"


@pytest.mark.asyncio
async def test_catalogue_preserves_degraded_render_reason_without_false_ready() -> None:
    metadata = _ready_render_metadata()
    metadata["supportability"] = {
        "state": "degraded",
        "reason": "render_supportability_draining",
        "deterministicOutputSupported": True,
        "templateRegistryReady": True,
        "runtimeAvailable": True,
    }
    service = ReportOrderingCatalogueService(render_client=_RenderMetadataClient(200, metadata))

    response = await service.get_catalogue()

    assert response.supportability.state == "partial"
    pdf = response.report_families[0].output_formats[1]
    assert pdf.state == "partial"
    assert pdf.reason_code == "render_supportability_draining"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,reason_code",
    [
        ({"supportedOutputFormats": ["pdf"]}, "render_supportability_invalid"),
        (
            {"supportability": {"state": "ready"}},
            "render_output_formats_invalid",
        ),
        (
            {
                "supportedOutputFormats": ["pdf", 1],
                "supportability": {"state": "ready"},
            },
            "render_output_formats_invalid",
        ),
    ],
)
async def test_catalogue_fails_pdf_closed_for_malformed_render_evidence(
    payload: dict[str, object], reason_code: str
) -> None:
    service = ReportOrderingCatalogueService(render_client=_RenderMetadataClient(200, payload))

    response = await service.get_catalogue()

    pdf = response.report_families[0].output_formats[1]
    assert pdf.state == "unavailable"
    assert pdf.reason_code == reason_code


@pytest.mark.parametrize(
    "definitions",
    [
        (),
        (REPORT_FAMILY_DEFINITIONS[0], REPORT_FAMILY_DEFINITIONS[0]),
        (
            replace(
                REPORT_FAMILY_DEFINITIONS[0],
                supported_output_formats=("xml",),
            ),
        ),
        (
            replace(
                REPORT_FAMILY_DEFINITIONS[0],
                supported_output_formats=("json",),
            ),
        ),
    ],
)
def test_catalogue_rejects_invalid_definition_truth(definitions) -> None:
    with pytest.raises(ReportCatalogueDefinitionError):
        ReportOrderingCatalogueService(
            render_client=_RenderMetadataClient(200, _ready_render_metadata()),
            definitions=definitions,
        )
