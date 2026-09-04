from dataclasses import replace

import pytest

from app.report_ordering_catalogue.definitions import REPORT_FAMILY_DEFINITIONS
from app.report_ordering_catalogue.service import (
    ReportCatalogueDefinitionError,
    ReportOrderingCatalogueService,
)


def _shipped_template_projection() -> dict[str, object]:
    """Entries shaped exactly like render#265's shipped GET /system/templates."""

    def entry(
        template_id, version, report_type, contract, status="active", publication="development"
    ):
        return {
            "template_id": template_id,
            "template_version": version,
            "status": status,
            "template_publication": publication,
            "published_at": None,
            "published_by": None,
            "supported_report_types": [report_type],
            "supported_report_data_contract_versions": [contract],
        }

    return {
        "templates": [
            entry(
                "portfolio-review",
                "v1",
                "portfolio_review",
                "portfolio_review.v1",
                publication="published",
            ),
            entry("portfolio-review", "v2", "portfolio_review", "portfolio_review.v1"),
            entry("proof-pack", "v1", "proof_pack", "dpm_proof_pack_report_input.v1"),
            entry("outcome-review", "v1", "outcome_review", "dpm_outcome_report_input.v1"),
            entry("rebalance-wave", "v1", "rebalance_wave", "dpm_wave_report_input.v1"),
        ]
    }


class _RenderMetadataClient:
    def __init__(
        self,
        status_code: int,
        payload: dict[str, object],
        *,
        templates_status: int = 200,
        templates_payload: dict[str, object] | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.templates_status = templates_status
        self.templates_payload = (
            templates_payload if templates_payload is not None else _shipped_template_projection()
        )
        self.calls: list[tuple[str | None, str | None]] = []

    async def get_metadata(self, correlation_id=None, trace_id=None):
        self.calls.append((correlation_id, trace_id))
        return self.status_code, self.payload

    async def get_template_projection(self, correlation_id=None, trace_id=None):
        return self.templates_status, self.templates_payload


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
    assert [section.business_label for section in portfolio_review.sections[:4]] == [
        "Client and mandate profile",
        "Portfolio overview",
        "Advisor commentary",
        "Allocation and portfolio construction",
    ]
    assert {field.field_id for field in portfolio_review.configuration_fields} == {
        "as_of_date",
        "advisor_brief_run_id",
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


@pytest.mark.asyncio
async def test_family_supportability_is_version_aware_not_global():
    """One family's missing template refuses ONLY that family: the others
    keep their evidence-backed readiness. The old seam applied one global
    render readiness to every family, which could state PDF support for a
    template version the renderer had never registered."""

    projection = _shipped_template_projection()
    projection["templates"] = [
        entry for entry in projection["templates"] if not (entry["template_id"] == "proof-pack")
    ]
    client = _RenderMetadataClient(200, _ready_render_metadata(), templates_payload=projection)
    service = ReportOrderingCatalogueService(render_client=client)

    catalogue = await service.get_catalogue()

    by_family = {item.report_family_id: item for item in catalogue.report_families}
    proof_pdf = _pdf_format(by_family["proof_pack"])
    review_pdf = _pdf_format(by_family["portfolio_review"])
    assert proof_pdf.state == "unavailable"
    assert proof_pdf.reason_code == "template_version_not_registered"
    assert review_pdf.state == "ready"


@pytest.mark.asyncio
async def test_a_development_template_is_fully_orderable_internally():
    """Publication is NOT rendering support: portfolio-review v2 is
    publication=development and must still be orderable - "can Render create
    it?" and "may the product distribute it externally?" are different
    questions, and this seam answers only the first."""

    client = _RenderMetadataClient(200, _ready_render_metadata())
    service = ReportOrderingCatalogueService(render_client=client)

    catalogue = await service.get_catalogue()

    by_family = {item.report_family_id: item for item in catalogue.report_families}
    assert _pdf_format(by_family["portfolio_review"]).state == "ready"


@pytest.mark.asyncio
async def test_unreadable_template_evidence_fails_every_pdf_family_visible():
    client = _RenderMetadataClient(200, _ready_render_metadata(), templates_status=503)
    service = ReportOrderingCatalogueService(render_client=client)

    catalogue = await service.get_catalogue()

    for item in catalogue.report_families:
        pdf = _pdf_format(item)
        assert pdf.state == "unavailable"
        assert pdf.reason_code == "render_templates_unavailable"


@pytest.mark.asyncio
async def test_a_non_renderable_status_refuses_new_orders_with_the_stated_status():
    projection = _shipped_template_projection()
    for entry in projection["templates"]:
        if entry["template_id"] == "portfolio-review" and entry["template_version"] == "v2":
            entry["status"] = "blocked_for_new_renders"
    client = _RenderMetadataClient(200, _ready_render_metadata(), templates_payload=projection)
    service = ReportOrderingCatalogueService(render_client=client)

    catalogue = await service.get_catalogue()

    by_family = {item.report_family_id: item for item in catalogue.report_families}
    pdf = _pdf_format(by_family["portfolio_review"])
    assert pdf.state == "unavailable"
    assert pdf.reason_code == "template_not_renderable"


def _pdf_format(item):
    return next(fmt for fmt in item.output_formats if fmt.format_id == "pdf")


@pytest.mark.asyncio
async def test_a_malformed_template_projection_fails_visible_like_an_unreachable_one():
    client = _RenderMetadataClient(
        200, _ready_render_metadata(), templates_payload={"templates": "not-a-list"}
    )
    service = ReportOrderingCatalogueService(render_client=client)

    catalogue = await service.get_catalogue()

    for item in catalogue.report_families:
        assert _pdf_format(item).reason_code == "render_templates_unavailable"


@pytest.mark.asyncio
async def test_type_and_contract_mismatches_refuse_with_their_own_reasons():
    projection = _shipped_template_projection()
    for entry in projection["templates"]:
        if entry["template_id"] == "portfolio-review":
            entry["supported_report_types"] = ["outcome_review"]
        if entry["template_id"] == "proof-pack":
            entry["supported_report_data_contract_versions"] = ["something_else.v9"]
    projection["templates"].append("not-a-dict")
    client = _RenderMetadataClient(200, _ready_render_metadata(), templates_payload=projection)
    service = ReportOrderingCatalogueService(render_client=client)

    catalogue = await service.get_catalogue()

    by_family = {item.report_family_id: item for item in catalogue.report_families}
    assert _pdf_format(by_family["portfolio_review"]).reason_code == "report_type_not_supported"
    assert _pdf_format(by_family["proof_pack"]).reason_code == "report_data_contract_not_supported"


@pytest.mark.asyncio
async def test_a_degraded_runtime_is_stated_before_template_verification():
    metadata = _ready_render_metadata()
    metadata["supportability"] = {
        "state": "degraded",
        "reason": "render_supportability_draining",
        "deterministicOutputSupported": True,
        "templateRegistryReady": True,
        "runtimeAvailable": True,
    }
    client = _RenderMetadataClient(200, metadata, templates_status=503)
    service = ReportOrderingCatalogueService(render_client=client)

    catalogue = await service.get_catalogue()

    # Runtime posture short-circuits: the families state the runtime fact and
    # never pretend template evidence was consulted.
    for item in catalogue.report_families:
        assert _pdf_format(item).state == "partial"
        assert _pdf_format(item).reason_code == "render_supportability_draining"
