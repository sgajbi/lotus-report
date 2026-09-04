from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from app.clients.render_client import RenderClient
from app.config import settings
from app.report_ordering_catalogue.definitions import (
    REPORT_FAMILY_DEFINITIONS,
    ReportConfigurationFieldDefinition,
    ReportFamilyDefinition,
    ReportSectionDefinition,
)
from app.report_ordering_catalogue.models import (
    ReportCatalogueSupportability,
    ReportConfigurationField,
    ReportConfigurationOption,
    ReportFamilyCatalogueItem,
    ReportOrderingCatalogueResponse,
    ReportOrderingMode,
    ReportOutputFormat,
    ReportSectionCatalogueItem,
)


class RenderMetadataClient(Protocol):
    async def get_metadata(
        self,
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]: ...

    async def get_template_projection(
        self,
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]: ...


class ReportCatalogueDefinitionError(ValueError):
    pass


class ReportOrderingCatalogueService:
    def __init__(
        self,
        *,
        render_client: RenderMetadataClient,
        definitions: Sequence[ReportFamilyDefinition] = REPORT_FAMILY_DEFINITIONS,
    ) -> None:
        self._render_client = render_client
        self._definitions = tuple(definitions)
        _validate_definitions(self._definitions)

    async def get_catalogue(
        self,
        *,
        correlation_id: str | None = None,
        trace_id: str | None = None,
    ) -> ReportOrderingCatalogueResponse:
        render_status, render_metadata = await self._render_client.get_metadata(
            correlation_id=correlation_id,
            trace_id=trace_id,
        )
        runtime_supportability = _pdf_supportability(render_status, render_metadata)
        templates_status, templates_payload = await self._render_client.get_template_projection(
            correlation_id=correlation_id,
            trace_id=trace_id,
        )
        templates = _template_projection_index(templates_status, templates_payload)
        report_families = [
            _family_item(
                definition,
                _family_pdf_supportability(
                    definition,
                    runtime=runtime_supportability,
                    templates=templates,
                ),
            )
            for definition in self._definitions
        ]
        return ReportOrderingCatalogueResponse(
            report_families=report_families,
            supportability=_catalogue_supportability(report_families),
        )


def build_report_ordering_catalogue_service() -> ReportOrderingCatalogueService:
    return ReportOrderingCatalogueService(
        render_client=RenderClient(
            base_url=settings.render_base_url,
            timeout_seconds=settings.upstream_timeout_seconds,
            max_retries=settings.upstream_max_retries,
            retry_backoff_seconds=settings.upstream_retry_backoff_seconds,
        )
    )


def _validate_definitions(definitions: tuple[ReportFamilyDefinition, ...]) -> None:
    if not definitions:
        raise ReportCatalogueDefinitionError("report catalogue must define at least one family")
    family_ids = [definition.report_family_id for definition in definitions]
    if len(family_ids) != len(set(family_ids)):
        raise ReportCatalogueDefinitionError("report catalogue family ids must be unique")
    for definition in definitions:
        if not definition.ordering_modes:
            raise ReportCatalogueDefinitionError(
                f"report catalogue family {definition.report_family_id} has no ordering mode"
            )
        if not definition.supported_output_formats:
            raise ReportCatalogueDefinitionError(
                f"report catalogue family {definition.report_family_id} has no output format"
            )
        unknown_formats = set(definition.supported_output_formats) - {"json", "pdf"}
        if unknown_formats:
            raise ReportCatalogueDefinitionError(
                f"report catalogue family {definition.report_family_id} has unknown output formats"
            )
        for mode in definition.ordering_modes:
            if mode.default_output_format not in definition.supported_output_formats:
                raise ReportCatalogueDefinitionError(
                    "report catalogue family "
                    f"{definition.report_family_id} has an invalid mode default"
                )
        _validate_sections(definition)


def _validate_sections(definition: ReportFamilyDefinition) -> None:
    section_ids = [section.section_id for section in definition.sections]
    if len(section_ids) != len(set(section_ids)):
        raise ReportCatalogueDefinitionError(
            f"report catalogue family {definition.report_family_id} has duplicate sections"
        )
    field_ids = {field.field_id for field in definition.configuration_fields}
    for section in definition.sections:
        if not set(section.dependency_field_ids).issubset(field_ids):
            raise ReportCatalogueDefinitionError(
                f"report catalogue section {section.section_id} has an unknown field dependency"
            )


def _pdf_supportability(
    status_code: int,
    metadata: dict[str, Any],
) -> ReportCatalogueSupportability:
    if status_code != 200:
        return ReportCatalogueSupportability(
            state="unavailable",
            reason_code="render_metadata_unavailable",
            message=(
                "Governed PDF creation is unavailable because rendering evidence could not be read."
            ),
        )
    supportability = metadata.get("supportability")
    if not isinstance(supportability, dict):
        return ReportCatalogueSupportability(
            state="unavailable",
            reason_code="render_supportability_invalid",
            message=(
                "Governed PDF creation is unavailable because rendering evidence is incomplete."
            ),
        )
    state = str(supportability.get("state") or "").lower()
    reason = str(supportability.get("reason") or "render_supportability_invalid")
    supported_formats = metadata.get("supportedOutputFormats")
    if not isinstance(supported_formats, list) or not all(
        isinstance(item, str) for item in supported_formats
    ):
        return ReportCatalogueSupportability(
            state="unavailable",
            reason_code="render_output_formats_invalid",
            message=(
                "Governed PDF creation is unavailable because output-format evidence is incomplete."
            ),
        )
    ready_evidence = (
        state == "ready"
        and supportability.get("deterministicOutputSupported") is True
        and supportability.get("templateRegistryReady") is True
        and supportability.get("runtimeAvailable") is True
        and "pdf" in supported_formats
    )
    if ready_evidence:
        return ReportCatalogueSupportability(
            state="ready",
            reason_code="render_supportability_ready",
            message="Governed PDF creation is available.",
        )
    if state == "degraded":
        return ReportCatalogueSupportability(
            state="partial",
            reason_code=reason,
            message="Governed PDF creation is temporarily degraded.",
        )
    return ReportCatalogueSupportability(
        state="unavailable",
        reason_code=reason,
        message="Governed PDF creation is unavailable.",
    )


def _template_projection_index(
    status_code: int,
    payload: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]] | None:
    """The shipped render#265 projection, indexed by (id, version).

    None means the evidence could not be read - every PDF-capable family
    then states unavailability rather than guessing.
    """

    if status_code != 200:
        return None
    entries = payload.get("templates")
    if not isinstance(entries, list):
        return None
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        template_id = str(entry.get("template_id") or "")
        template_version = str(entry.get("template_version") or "")
        if template_id and template_version:
            index[(template_id, template_version)] = entry
    return index


def _family_pdf_supportability(
    definition: ReportFamilyDefinition,
    *,
    runtime: ReportCatalogueSupportability,
    templates: dict[tuple[str, str], dict[str, Any]] | None,
) -> ReportCatalogueSupportability:
    """Version-aware: can Render create THIS family's intended template?

    Proves the exact (template_id, template_version) the family orders is
    registered, renderable for new renders, and supports the family's report
    type and report_data contract. Publication posture is deliberately NOT
    consulted: a development template is fully orderable for internal use -
    "can Render create it?" and "may the product distribute it externally?"
    are different questions, and this seam answers only the first.
    """

    if "pdf" not in definition.supported_output_formats:
        return runtime
    if runtime.state != "ready":
        return runtime
    if templates is None:
        return ReportCatalogueSupportability(
            state="unavailable",
            reason_code="render_templates_unavailable",
            message=(
                "Governed PDF creation is unavailable because template evidence could not be read."
            ),
        )
    entry = templates.get((definition.template_id, definition.template_version))
    if entry is None:
        return ReportCatalogueSupportability(
            state="unavailable",
            reason_code="template_version_not_registered",
            message=(
                f"Governed PDF creation is unavailable because template "
                f"{definition.template_id}/{definition.template_version} is not "
                "registered with the renderer."
            ),
        )
    if str(entry.get("status") or "") != "active":
        return ReportCatalogueSupportability(
            state="unavailable",
            reason_code="template_not_renderable",
            message=(
                f"Governed PDF creation is unavailable because template "
                f"{definition.template_id}/{definition.template_version} is "
                f"{entry.get('status') or 'in an unstated status'} for new renders."
            ),
        )
    supported_types = entry.get("supported_report_types")
    if not isinstance(supported_types, list) or definition.report_type not in supported_types:
        return ReportCatalogueSupportability(
            state="unavailable",
            reason_code="report_type_not_supported",
            message=(
                f"Governed PDF creation is unavailable because template "
                f"{definition.template_id}/{definition.template_version} does not "
                f"support report type {definition.report_type}."
            ),
        )
    supported_contracts = entry.get("supported_report_data_contract_versions")
    if (
        not isinstance(supported_contracts, list)
        or definition.report_data_contract_version not in supported_contracts
    ):
        return ReportCatalogueSupportability(
            state="unavailable",
            reason_code="report_data_contract_not_supported",
            message=(
                f"Governed PDF creation is unavailable because template "
                f"{definition.template_id}/{definition.template_version} does not "
                f"accept contract {definition.report_data_contract_version}."
            ),
        )
    return runtime


def _family_item(
    definition: ReportFamilyDefinition,
    pdf_supportability: ReportCatalogueSupportability,
) -> ReportFamilyCatalogueItem:
    output_formats = [
        _output_format(format_id, pdf_supportability)
        for format_id in definition.supported_output_formats
    ]
    family_supportability = _family_supportability(output_formats)
    return ReportFamilyCatalogueItem(
        report_family_id=definition.report_family_id,
        business_label=definition.business_label,
        description=definition.description,
        intended_use=definition.intended_use,
        audience_roles=list(definition.audience_roles),
        client_release_posture=definition.client_release_posture,
        ordering_modes=[
            ReportOrderingMode(
                mode_id=mode.mode_id,
                business_label=mode.business_label,
                description=mode.description,
                default_output_format=mode.default_output_format,
                interactive=mode.interactive,
            )
            for mode in definition.ordering_modes
        ],
        output_formats=output_formats,
        configuration_fields=[
            _configuration_field(field) for field in definition.configuration_fields
        ],
        sections=[_section_item(section) for section in definition.sections],
        supportability=family_supportability,
    )


def _output_format(
    format_id: str,
    pdf_supportability: ReportCatalogueSupportability,
) -> ReportOutputFormat:
    if format_id == "json":
        return ReportOutputFormat(
            format_id="json",
            business_label="Structured data package",
            use_posture="system_integration",
            state="ready",
            reason_code="report_data_ready",
        )
    return ReportOutputFormat(
        format_id="pdf",
        business_label="Governed PDF document",
        use_posture="governed_document",
        state=pdf_supportability.state,
        reason_code=pdf_supportability.reason_code,
    )


def _family_supportability(
    output_formats: list[ReportOutputFormat],
) -> ReportCatalogueSupportability:
    if all(output_format.state == "ready" for output_format in output_formats):
        return ReportCatalogueSupportability(
            state="ready",
            reason_code="report_family_ready",
            message="Available within its supported reporting workflow.",
        )
    if any(output_format.state == "ready" for output_format in output_formats):
        return ReportCatalogueSupportability(
            state="partial",
            reason_code="report_family_output_partial",
            message="Structured data remains available; governed document creation is not ready.",
        )
    return ReportCatalogueSupportability(
        state="unavailable",
        reason_code="report_family_unavailable",
        message="This report is currently unavailable.",
    )


def _catalogue_supportability(
    report_families: list[ReportFamilyCatalogueItem],
) -> ReportCatalogueSupportability:
    if all(family.supportability.state == "ready" for family in report_families):
        return ReportCatalogueSupportability(
            state="ready",
            reason_code="report_catalogue_ready",
            message="All published report families are available in their supported workflows.",
        )
    if any(family.supportability.state != "unavailable" for family in report_families):
        return ReportCatalogueSupportability(
            state="partial",
            reason_code="report_catalogue_partial",
            message="Some report outputs are temporarily unavailable or degraded.",
        )
    return ReportCatalogueSupportability(
        state="unavailable",
        reason_code="report_catalogue_unavailable",
        message="No published report family is currently available.",
    )


def _configuration_field(
    definition: ReportConfigurationFieldDefinition,
) -> ReportConfigurationField:
    return ReportConfigurationField(
        field_id=definition.field_id,
        business_label=definition.business_label,
        description=definition.description,
        input_type=definition.input_type,
        requirement=definition.requirement,
        defaulting_policy=definition.defaulting_policy,
        value_source=definition.value_source,
        options=[
            ReportConfigurationOption(value=option.value, business_label=option.business_label)
            for option in definition.options
        ],
    )


def _section_item(definition: ReportSectionDefinition) -> ReportSectionCatalogueItem:
    return ReportSectionCatalogueItem(
        section_id=definition.section_id,
        business_label=definition.business_label,
        description=definition.description,
        display_order=definition.display_order,
        selection_posture=definition.selection_posture,
        default_selected=definition.default_selected,
        dependency_field_ids=list(definition.dependency_field_ids),
    )
