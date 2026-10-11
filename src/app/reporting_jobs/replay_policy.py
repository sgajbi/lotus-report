"""Bounded failed-work recovery families and immutable composite request reconstruction."""

from typing import Any

from app.composite_reporting.invocation_authority import BINDING_OPTION
from app.composite_reporting.models import CompositeReviewJobRequest
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportJobLedgerRecord


def archive_absence_confirmed(payload: dict[str, Any]) -> bool:
    """Archive's by-request-id 404 contract, never a proxy/route or binary-missing 404."""
    error = payload.get("error")
    return isinstance(error, dict) and error.get("code") == "document_not_found"


def resolution_replay_supported(job: ReportJobLedgerRecord) -> bool:
    return job.report_type == "portfolio_review" or (
        job.report_type == "composite_review"
        and job.requested_output_formats == ["xlsx"]
        and job.accepted_document_contract is not None
        and job.accepted_document_contract.get("report_data_contract_version")
        == "composite_review.v8"
        and job.render_template_id == "composite-review"
        and job.render_template_version == "v8"
    )


def composite_replay_request(job: ReportJobLedgerRecord) -> CompositeReviewJobRequest:
    options = dict(job.options)
    selection = options.pop("composite_selection", None)
    context = options.pop("composite_source_context", None)
    products = options.pop("composite_source_products", None)
    options.pop(BINDING_OPTION, None)
    return CompositeReviewJobRequest.model_validate(
        {
            "selection": selection,
            "source_context": context,
            "source_products": products,
            "requested_output_formats": job.requested_output_formats,
            "options": options,
        }
    )


def replay_request(
    job: ReportJobLedgerRecord,
) -> CompositeReviewJobRequest | PortfolioReviewJobRequest:
    if job.report_type == "composite_review":
        return composite_replay_request(job)
    return PortfolioReviewJobRequest(
        portfolio_scope=job.portfolio_scope,
        as_of_date=job.as_of_date,
        requested_output_formats=job.requested_output_formats,
        reporting_currency=job.reporting_currency,
        options=job.options,
    )
