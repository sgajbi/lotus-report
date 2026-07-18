from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReportSectionDefinition:
    section_id: str
    response_section_id: str
    response_title: str
    business_label: str
    description: str
    response_key: str
    display_order: int
    selection_posture: str
    default_selected: bool
    dependency_field_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportOrderingModeDefinition:
    mode_id: str
    business_label: str
    description: str
    default_output_format: str
    interactive: bool


@dataclass(frozen=True)
class ReportConfigurationOptionDefinition:
    value: str
    business_label: str


@dataclass(frozen=True)
class ReportConfigurationFieldDefinition:
    field_id: str
    business_label: str
    description: str
    input_type: str
    requirement: str
    defaulting_policy: str
    value_source: str
    options: tuple[ReportConfigurationOptionDefinition, ...] = ()


@dataclass(frozen=True)
class ReportFamilyDefinition:
    report_family_id: str
    report_type: str
    business_label: str
    description: str
    intended_use: str
    audience_roles: tuple[str, ...]
    client_release_posture: str
    template_id: str
    template_version: str
    ordering_modes: tuple[ReportOrderingModeDefinition, ...]
    supported_output_formats: tuple[str, ...]
    configuration_fields: tuple[ReportConfigurationFieldDefinition, ...] = ()
    sections: tuple[ReportSectionDefinition, ...] = ()


PORTFOLIO_REVIEW_SECTION_DEFINITIONS = (
    ReportSectionDefinition(
        section_id="CLIENT_PROFILE",
        response_section_id="client_profile",
        response_title="Client And Mandate Profile",
        business_label="Client and mandate profile",
        description="Client, relationship, booking centre, portfolio, and mandate context.",
        response_key="clientProfile",
        display_order=10,
        selection_posture="required",
        default_selected=True,
    ),
    ReportSectionDefinition(
        section_id="OVERVIEW",
        response_section_id="executive_summary",
        response_title="Executive Review Summary",
        business_label="Portfolio overview",
        description="Headline portfolio value, cash, investment, and review context.",
        response_key="overview",
        display_order=20,
        selection_posture="optional",
        default_selected=True,
    ),
    ReportSectionDefinition(
        section_id="ALLOCATION",
        response_section_id="asset_allocation",
        response_title="Asset Allocation And Portfolio Construction",
        business_label="Allocation and portfolio construction",
        description="Portfolio allocation across the selected business dimensions.",
        response_key="allocation",
        display_order=30,
        selection_posture="optional",
        default_selected=True,
        dependency_field_ids=("allocation_dimensions",),
    ),
    ReportSectionDefinition(
        section_id="PERFORMANCE",
        response_section_id="performance_review",
        response_title="Performance Review",
        business_label="Performance review",
        description="Portfolio returns, benchmark comparison, and performance contribution.",
        response_key="performance",
        display_order=40,
        selection_posture="optional",
        default_selected=True,
        dependency_field_ids=("benchmark_code",),
    ),
    ReportSectionDefinition(
        section_id="RISK_ANALYTICS",
        response_section_id="risk_review",
        response_title="Risk Review",
        business_label="Risk review",
        description="Portfolio risk, drawdown, concentration, and benchmark-relative measures.",
        response_key="riskAnalytics",
        display_order=50,
        selection_posture="optional",
        default_selected=True,
        dependency_field_ids=("benchmark_code",),
    ),
    ReportSectionDefinition(
        section_id="INCOME_AND_ACTIVITY",
        response_section_id="income_cash_activity",
        response_title="Income, Cash, And Activity",
        business_label="Income, cash and activity",
        description="Income, fees, cash flow, realised results, and portfolio activity.",
        response_key="incomeAndActivity",
        display_order=60,
        selection_posture="optional",
        default_selected=True,
    ),
    ReportSectionDefinition(
        section_id="HOLDINGS",
        response_section_id="holdings_appendix",
        response_title="Holdings Appendix",
        business_label="Holdings detail",
        description="Position-level holdings, weights, valuation, and source-backed results.",
        response_key="holdings",
        display_order=70,
        selection_posture="optional",
        default_selected=True,
    ),
    ReportSectionDefinition(
        section_id="TRANSACTIONS",
        response_section_id="transactions_appendix",
        response_title="Transactions Appendix",
        business_label="Transaction activity",
        description="Transaction history, settlement context, and realised activity evidence.",
        response_key="transactions",
        display_order=80,
        selection_posture="optional",
        default_selected=True,
    ),
)


_PORTFOLIO_REVIEW_FIELDS = (
    ReportConfigurationFieldDefinition(
        field_id="as_of_date",
        business_label="Report date",
        description="Business date used for holdings, activity, performance, and risk evidence.",
        input_type="business_date",
        requirement="required",
        defaulting_policy="caller_required",
        value_source="caller",
    ),
    ReportConfigurationFieldDefinition(
        field_id="reporting_currency",
        business_label="Reporting currency",
        description="Currency used for portfolio-level monetary values.",
        input_type="currency",
        requirement="optional",
        defaulting_policy="portfolio_currency_when_omitted",
        value_source="portfolio_context_or_caller",
    ),
    ReportConfigurationFieldDefinition(
        field_id="benchmark_code",
        business_label="Comparison benchmark",
        description="Approved benchmark used for relative performance and risk measures.",
        input_type="benchmark",
        requirement="optional",
        defaulting_policy="portfolio_benchmark_when_omitted",
        value_source="gateway_eligible_benchmark",
    ),
    ReportConfigurationFieldDefinition(
        field_id="allocation_dimensions",
        business_label="Allocation views",
        description="Business dimensions used to group portfolio allocation.",
        input_type="multi_select",
        requirement="optional",
        defaulting_policy="asset_class_when_omitted",
        value_source="report_catalogue",
        options=(
            ReportConfigurationOptionDefinition("asset_class", "Asset class"),
            ReportConfigurationOptionDefinition("currency", "Currency"),
            ReportConfigurationOptionDefinition("sector", "Sector"),
            ReportConfigurationOptionDefinition("country", "Country"),
            ReportConfigurationOptionDefinition("region", "Region"),
            ReportConfigurationOptionDefinition("product_type", "Product type"),
            ReportConfigurationOptionDefinition("rating", "Credit rating"),
        ),
    ),
)


REPORT_FAMILY_DEFINITIONS = (
    ReportFamilyDefinition(
        report_family_id="portfolio_review",
        report_type="portfolio_review",
        business_label="Portfolio review report",
        description="Advisor review pack for a client portfolio and selected business date.",
        intended_use="advisor_client_portfolio_review",
        audience_roles=("client_advisor", "portfolio_manager"),
        client_release_posture="advisor_review_required_distribution_not_supported",
        template_id="portfolio-review",
        template_version="v1",
        ordering_modes=(
            ReportOrderingModeDefinition(
                mode_id="single_portfolio",
                business_label="Single portfolio",
                description="Create one report for the selected portfolio.",
                default_output_format="json",
                interactive=True,
            ),
            ReportOrderingModeDefinition(
                mode_id="explicit_portfolio_batch",
                business_label="Selected portfolio batch",
                description="Create the same report for an explicit portfolio list.",
                default_output_format="pdf",
                interactive=False,
            ),
            ReportOrderingModeDefinition(
                mode_id="governed_schedule",
                business_label="Governed reporting schedule",
                description="Create reports from an operations-managed reporting schedule.",
                default_output_format="pdf",
                interactive=False,
            ),
        ),
        supported_output_formats=("json", "pdf"),
        configuration_fields=_PORTFOLIO_REVIEW_FIELDS,
        sections=PORTFOLIO_REVIEW_SECTION_DEFINITIONS,
    ),
    ReportFamilyDefinition(
        report_family_id="proof_pack",
        report_type="proof_pack",
        business_label="Pre-trade decision evidence",
        description="Governed evidence pack created from an approved portfolio-management review.",
        intended_use="portfolio_management_pre_trade_control",
        audience_roles=("portfolio_manager", "investment_control", "audit"),
        client_release_posture="internal_control_only",
        template_id="proof-pack",
        template_version="v1",
        ordering_modes=(
            ReportOrderingModeDefinition(
                mode_id="source_workflow",
                business_label="Portfolio-management workflow",
                description="Created from a source-owned pre-trade decision workflow.",
                default_output_format="pdf",
                interactive=False,
            ),
        ),
        supported_output_formats=("json", "pdf"),
    ),
    ReportFamilyDefinition(
        report_family_id="rebalance_wave",
        report_type="rebalance_wave",
        business_label="Rebalance wave evidence",
        description="Governed evidence for a managed portfolio rebalance wave.",
        intended_use="portfolio_management_rebalance_control",
        audience_roles=("portfolio_manager", "operations", "audit"),
        client_release_posture="internal_control_only",
        template_id="rebalance-wave",
        template_version="v1",
        ordering_modes=(
            ReportOrderingModeDefinition(
                mode_id="source_workflow",
                business_label="Portfolio-management workflow",
                description="Created from a source-owned rebalance-wave workflow.",
                default_output_format="pdf",
                interactive=False,
            ),
        ),
        supported_output_formats=("json", "pdf"),
    ),
    ReportFamilyDefinition(
        report_family_id="outcome_review",
        report_type="outcome_review",
        business_label="Post-trade outcome review",
        description="Governed review of realised outcomes against approved pre-trade evidence.",
        intended_use="portfolio_management_post_trade_control",
        audience_roles=("portfolio_manager", "investment_control", "audit"),
        client_release_posture="internal_control_only",
        template_id="outcome-review",
        template_version="v1",
        ordering_modes=(
            ReportOrderingModeDefinition(
                mode_id="source_workflow",
                business_label="Portfolio-management workflow",
                description="Created from a source-owned post-trade outcome workflow.",
                default_output_format="pdf",
                interactive=False,
            ),
        ),
        supported_output_formats=("json", "pdf"),
    ),
)
