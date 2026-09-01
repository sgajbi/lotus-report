import pytest
from pydantic import ValidationError

from app.report_ordering_catalogue.validation import (
    ReportOrderingSubmissionError,
    validate_report_ordering_submission,
)
from app.reporting_jobs.models import PortfolioReviewJobRequest


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


def test_advisor_commentary_can_be_ordered_as_pdf_on_both_paths() -> None:
    """The temporary render gate is gone, and it had to go from BOTH paths.

    It stood while lotus-render drew the section without the per-claim
    grounding marker, so an ungrounded AI claim was distinguishable only by
    contrast with grounded points on the same page - and a PDF is archived, so
    an unverifiable claim that looks verifiable becomes durable evidence.
    lotus-render#226 draws it, so the gate has nothing left to hold.

    Orders arrive by two paths and each carried its own copy of that gate. One
    removed alone would leave PDF orders refused on one path and accepted on
    the other, so both are asserted here rather than only the path this file
    owns.
    """

    _validate(
        formats=["json", "pdf"],
        options={
            "sections": ["ADVISOR_COMMENTARY"],
            "advisor_brief_run_id": "run_accept_1",
        },
    )

    PortfolioReviewJobRequest(
        portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        as_of_date="2026-04-22",
        requested_output_formats=["json", "pdf"],
        reporting_currency="USD",
        options={
            "sections": ["ADVISOR_COMMENTARY"],
            "advisor_brief_run_id": "run_accept_1",
        },
    )


def test_a_pdf_order_still_requires_the_accepted_run_it_composes() -> None:
    """Opening the PDF path does not loosen what the section is sourced from.

    The run id stays required on both paths: the section is composed only from
    an accepted Advisor Brief run, and lotus-report never chooses one
    implicitly. Removing an output-format gate must not quietly remove that.
    """

    with pytest.raises(ReportOrderingSubmissionError) as excinfo:
        _validate(formats=["json", "pdf"], options={"sections": ["ADVISOR_COMMENTARY"]})
    assert excinfo.value.code == "missing_conditional_report_field"

    with pytest.raises(ValidationError):
        PortfolioReviewJobRequest(
            portfolio_scope={"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
            as_of_date="2026-04-22",
            requested_output_formats=["json", "pdf"],
            reporting_currency="USD",
            options={"sections": ["ADVISOR_COMMENTARY"]},
        )
