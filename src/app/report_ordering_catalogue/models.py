from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

REPORT_ORDERING_CATALOGUE_EXAMPLE = {
    "source_service": "lotus-report",
    "contract_version": "report-ordering-catalogue.v1",
    "report_families": [
        {
            "report_family_id": "portfolio_review",
            "business_label": "Portfolio review report",
            "description": "Advisor review pack for a client portfolio and selected business date.",
            "intended_use": "advisor_client_portfolio_review",
            "audience_roles": ["client_advisor", "portfolio_manager"],
            "client_release_posture": ("advisor_review_required_distribution_not_supported"),
            "ordering_modes": [
                {
                    "mode_id": "single_portfolio",
                    "business_label": "Single portfolio",
                    "description": "Create one report for the selected portfolio.",
                    "default_output_format": "json",
                    "interactive": True,
                }
            ],
            "output_formats": [
                {
                    "format_id": "json",
                    "business_label": "Structured data package",
                    "use_posture": "system_integration",
                    "state": "ready",
                    "reason_code": "report_data_ready",
                },
                {
                    "format_id": "pdf",
                    "business_label": "Governed PDF document",
                    "use_posture": "governed_document",
                    "state": "ready",
                    "reason_code": "render_supportability_ready",
                },
            ],
            "configuration_fields": [
                {
                    "field_id": "as_of_date",
                    "business_label": "Report date",
                    "description": (
                        "Business date used for holdings, activity, performance, and risk evidence."
                    ),
                    "input_type": "business_date",
                    "requirement": "required",
                    "defaulting_policy": "caller_required",
                    "value_source": "caller",
                    "options": [],
                }
            ],
            "sections": [
                {
                    "section_id": "CLIENT_PROFILE",
                    "business_label": "Client and mandate profile",
                    "description": (
                        "Client, relationship, booking centre, portfolio, and mandate context."
                    ),
                    "display_order": 10,
                    "selection_posture": "required",
                    "default_selected": True,
                    "dependency_field_ids": [],
                }
            ],
            "supportability": {
                "state": "ready",
                "reason_code": "report_family_ready",
                "message": "Available within its supported reporting workflow.",
            },
        }
    ],
    "supportability": {
        "state": "ready",
        "reason_code": "report_catalogue_ready",
        "message": "All published report families are available in their supported workflows.",
    },
}


class CatalogueModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportConfigurationOption(CatalogueModel):
    value: str = Field(description="Stable option value accepted by the report contract.")
    business_label: str = Field(description="Business label shown to product users.")


class ReportConfigurationField(CatalogueModel):
    field_id: str = Field(description="Stable configuration field identifier.")
    business_label: str = Field(description="Business label shown to product users.")
    description: str = Field(description="Business meaning of the report configuration.")
    input_type: Literal["business_date", "currency", "benchmark", "multi_select", "text"]
    requirement: Literal["required", "optional", "conditional"]
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
