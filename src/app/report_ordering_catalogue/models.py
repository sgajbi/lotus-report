from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CatalogueModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportConfigurationOption(CatalogueModel):
    value: str = Field(description="Stable option value accepted by the report contract.")
    business_label: str = Field(description="Business label shown to product users.")


class ReportConfigurationField(CatalogueModel):
    field_id: str = Field(description="Stable configuration field identifier.")
    business_label: str = Field(description="Business label shown to product users.")
    description: str = Field(description="Business meaning of the report configuration.")
    input_type: Literal["business_date", "currency", "benchmark", "multi_select"]
    requirement: Literal["required", "optional"]
    defaulting_policy: str = Field(description="Stable policy used when no value is supplied.")
    value_source: Literal[
        "caller",
        "portfolio_context_or_caller",
        "gateway_eligible_benchmark",
        "report_catalogue",
    ]
    options: list[ReportConfigurationOption] = Field(default_factory=list)


class ReportSectionCatalogueItem(CatalogueModel):
    section_id: str = Field(description="Stable section value accepted by Report.")
    business_label: str = Field(description="Business section label shown to product users.")
    description: str = Field(description="Business content covered by the section.")
    display_order: int = Field(ge=1)
    selection_posture: Literal["required", "optional"]
    default_selected: bool
    dependency_field_ids: list[str] = Field(default_factory=list)


class ReportOrderingMode(CatalogueModel):
    mode_id: Literal[
        "single_portfolio",
        "explicit_portfolio_batch",
        "governed_schedule",
        "source_workflow",
    ]
    business_label: str
    description: str
    default_output_format: Literal["json", "pdf"]
    interactive: bool


class ReportOutputFormat(CatalogueModel):
    format_id: Literal["json", "pdf"]
    business_label: str
    use_posture: Literal["system_integration", "governed_document"]
    state: Literal["ready", "partial", "unavailable"]
    reason_code: str


class ReportCatalogueSupportability(CatalogueModel):
    state: Literal["ready", "partial", "unavailable"]
    reason_code: str
    message: str


class ReportFamilyCatalogueItem(CatalogueModel):
    report_family_id: str
    business_label: str
    description: str
    intended_use: str
    audience_roles: list[str]
    client_release_posture: Literal[
        "advisor_review_required_distribution_not_supported",
        "internal_control_only",
    ]
    ordering_modes: list[ReportOrderingMode]
    output_formats: list[ReportOutputFormat]
    configuration_fields: list[ReportConfigurationField] = Field(default_factory=list)
    sections: list[ReportSectionCatalogueItem] = Field(default_factory=list)
    supportability: ReportCatalogueSupportability


class ReportOrderingCatalogueResponse(CatalogueModel):
    source_service: Literal["lotus-report"] = "lotus-report"
    contract_version: Literal["report-ordering-catalogue.v1"] = "report-ordering-catalogue.v1"
    report_families: list[ReportFamilyCatalogueItem]
    supportability: ReportCatalogueSupportability
