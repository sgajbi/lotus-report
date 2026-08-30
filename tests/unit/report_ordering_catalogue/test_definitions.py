from app.report_ordering_catalogue.definitions import (
    PORTFOLIO_REVIEW_SECTION_DEFINITIONS,
    REPORT_FAMILY_DEFINITIONS,
)


def test_report_family_definitions_are_unique_and_implementation_backed() -> None:
    family_ids = [definition.report_family_id for definition in REPORT_FAMILY_DEFINITIONS]
    report_types = [definition.report_type for definition in REPORT_FAMILY_DEFINITIONS]
    template_keys = [
        (definition.template_id, definition.template_version)
        for definition in REPORT_FAMILY_DEFINITIONS
    ]

    assert family_ids == [
        "portfolio_review",
        "proof_pack",
        "rebalance_wave",
        "outcome_review",
    ]
    assert len(family_ids) == len(set(family_ids))
    assert len(report_types) == len(set(report_types))
    assert len(template_keys) == len(set(template_keys))


def test_portfolio_review_sections_preserve_runtime_order_and_selection_truth() -> None:
    assert [section.section_id for section in PORTFOLIO_REVIEW_SECTION_DEFINITIONS] == [
        "CLIENT_PROFILE",
        "OVERVIEW",
        "ADVISOR_COMMENTARY",
        "ALLOCATION",
        "PERFORMANCE",
        "RISK_ANALYTICS",
        "INCOME_AND_ACTIVITY",
        "HOLDINGS",
        "TRANSACTIONS",
    ]
    assert [section.display_order for section in PORTFOLIO_REVIEW_SECTION_DEFINITIONS] == [
        10,
        20,
        25,
        30,
        40,
        50,
        60,
        70,
        80,
    ]
    assert PORTFOLIO_REVIEW_SECTION_DEFINITIONS[0].selection_posture == "required"
    # ADVISOR_COMMENTARY is the one opt-in section: reviewed AI-assisted
    # narrative enters the pack only when the caller names an accepted brief.
    by_id = {section.section_id: section for section in PORTFOLIO_REVIEW_SECTION_DEFINITIONS}
    assert by_id["ADVISOR_COMMENTARY"].default_selected is False
    assert by_id["ADVISOR_COMMENTARY"].selection_posture == "optional"
    assert by_id["ADVISOR_COMMENTARY"].dependency_field_ids == ("advisor_brief_run_id",)
    assert all(
        section.default_selected
        for section in PORTFOLIO_REVIEW_SECTION_DEFINITIONS
        if section.section_id != "ADVISOR_COMMENTARY"
    )


def test_only_portfolio_review_is_directly_orderable() -> None:
    interactive_families = [
        definition.report_family_id
        for definition in REPORT_FAMILY_DEFINITIONS
        if any(mode.interactive for mode in definition.ordering_modes)
    ]

    assert interactive_families == ["portfolio_review"]
    assert all(
        definition.client_release_posture == "internal_control_only"
        for definition in REPORT_FAMILY_DEFINITIONS
        if definition.report_family_id != "portfolio_review"
    )


def test_ordering_mode_defaults_match_current_submission_contracts() -> None:
    mode_defaults = {
        (definition.report_family_id, mode.mode_id): mode.default_output_format
        for definition in REPORT_FAMILY_DEFINITIONS
        for mode in definition.ordering_modes
    }

    assert mode_defaults[("portfolio_review", "single_portfolio")] == "json"
    assert mode_defaults[("portfolio_review", "explicit_portfolio_batch")] == "pdf"
    assert mode_defaults[("portfolio_review", "governed_schedule")] == "pdf"
    assert all(
        default_format == "pdf"
        for (family_id, mode_id), default_format in mode_defaults.items()
        if mode_id == "source_workflow" and family_id != "portfolio_review"
    )
