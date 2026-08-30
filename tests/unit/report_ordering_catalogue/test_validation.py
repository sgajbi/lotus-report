import pytest

from app.report_ordering_catalogue.validation import (
    ReportOrderingSubmissionError,
    validate_report_ordering_submission,
)


def _validate(
    *,
    family: str = "portfolio_review",
    mode: str = "single_portfolio",
    formats: list[str] | None = None,
    options: dict[str, object] | None = None,
) -> None:
    validate_report_ordering_submission(
        report_family_id=family,
        ordering_mode_id=mode,
        requested_output_formats=formats if formats is not None else ["json"],
        options=options if options is not None else {},
    )


def test_accepts_source_backed_portfolio_review_configuration() -> None:
    _validate(
        formats=["json", "pdf"],
        options={
            "sections": ["OVERVIEW", "PERFORMANCE"],
            "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
            "allocation_dimensions": ["asset_class", "currency"],
        },
    )


@pytest.mark.parametrize(
    "family,mode,formats,options,expected_code",
    [
        ("unknown", "single_portfolio", ["json"], {}, "unknown_report_family"),
        (
            "portfolio_review",
            "source_workflow",
            ["json"],
            {},
            "unsupported_report_ordering_mode",
        ),
        (
            "portfolio_review",
            "single_portfolio",
            ["xlsx"],
            {},
            "unsupported_report_output_format",
        ),
        (
            "portfolio_review",
            "single_portfolio",
            ["json", "json"],
            {},
            "duplicate_report_output_format",
        ),
        (
            "portfolio_review",
            "single_portfolio",
            ["json"],
            {"sections": ["CLIENT_STATEMENT"]},
            "unsupported_report_section",
        ),
        (
            "portfolio_review",
            "single_portfolio",
            ["json"],
            {"allocation_dimensions": ["legal_entity"]},
            "unsupported_allocation_dimension",
        ),
        (
            "portfolio_review",
            "single_portfolio",
            ["json"],
            {"template_id": "unapproved-template"},
            "unsupported_report_configuration",
        ),
        (
            "proof_pack",
            "source_workflow",
            ["pdf"],
            {"sections": ["MANDATE_CONTEXT"]},
            "unsupported_report_configuration",
        ),
    ],
)
def test_rejects_configuration_not_published_for_the_selected_workflow(
    family: str,
    mode: str,
    formats: list[str],
    options: dict[str, object],
    expected_code: str,
) -> None:
    with pytest.raises(ReportOrderingSubmissionError) as exc_info:
        _validate(family=family, mode=mode, formats=formats, options=options)

    assert exc_info.value.code == expected_code


def test_accepts_bounded_operational_retention_controls_without_publishing_templates() -> None:
    _validate(
        family="outcome_review",
        mode="source_workflow",
        formats=["pdf"],
        options={
            "retention_policy_id": "generated-report-standard",
            "retain_until_date": "2033-04-22",
        },
    )


def test_accepts_bounded_manifest_provenance_for_explicit_batch_only() -> None:
    _validate(
        mode="explicit_portfolio_batch",
        formats=["pdf"],
        options={
            "batch_manifest_source": "ops-manifest-apac-monthly",
            "batch_manifest_version": "2026-07",
            "batch_manifest_hash": "sha256:manifest-001",
        },
    )

    with pytest.raises(ReportOrderingSubmissionError) as exc_info:
        _validate(
            mode="single_portfolio",
            options={"batch_manifest_hash": "sha256:manifest-001"},
        )

    assert exc_info.value.code == "unsupported_report_configuration"


def test_advisor_commentary_dependency_enforced_at_ordering_acceptance() -> None:
    """Acceptance must refuse what dispatch would refuse: a durably accepted
    batch selecting ADVISOR_COMMENTARY without the run id would strand its
    items at materialization (issue #166)."""

    with pytest.raises(ReportOrderingSubmissionError) as excinfo:
        _validate(options={"sections": ["OVERVIEW", "ADVISOR_COMMENTARY"]})
    assert excinfo.value.code == "missing_conditional_report_field"

    with pytest.raises(ReportOrderingSubmissionError) as excinfo:
        _validate(
            options={
                "sections": ["ADVISOR_COMMENTARY"],
                "advisor_brief_run_id": "   ",
            }
        )
    assert excinfo.value.code == "missing_conditional_report_field"

    _validate(
        options={
            "sections": ["OVERVIEW", "ADVISOR_COMMENTARY"],
            "advisor_brief_run_id": "run_accept_1",
        }
    )


def test_advisor_commentary_refuses_pdf_until_render_template_exists() -> None:
    """Temporary render gate: a PDF silently omitting an ordered section is a
    misleading client document, so PDF orders refuse the section explicitly."""

    with pytest.raises(ReportOrderingSubmissionError) as excinfo:
        _validate(
            formats=["json", "pdf"],
            options={
                "sections": ["ADVISOR_COMMENTARY"],
                "advisor_brief_run_id": "run_accept_1",
            },
        )
    assert excinfo.value.code == "report_section_output_format_unsupported"

    # Benchmark-dependent sections stay orderable without their optional
    # dependency - only `conditional` fields are required-when-selected.
    _validate(formats=["json", "pdf"], options={"sections": ["PERFORMANCE"]})
