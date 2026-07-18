import pytest
from pydantic import ValidationError

from app.report_ordering_catalogue.models import (
    ReportCatalogueSupportability,
    ReportFamilyCatalogueItem,
)


def test_catalogue_models_reject_free_form_primary_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ReportFamilyCatalogueItem.model_validate(
            {
                "report_family_id": "portfolio_review",
                "business_label": "Portfolio review report",
                "description": "Advisor review pack.",
                "intended_use": "advisor_client_portfolio_review",
                "audience_roles": ["client_advisor"],
                "client_release_posture": ("advisor_review_required_distribution_not_supported"),
                "ordering_modes": [],
                "output_formats": [],
                "supportability": {
                    "state": "ready",
                    "reason_code": "report_family_ready",
                    "message": "Available for ordering.",
                },
                "options": {"arbitrary": True},
            }
        )


def test_catalogue_supportability_uses_bounded_states() -> None:
    with pytest.raises(ValidationError):
        ReportCatalogueSupportability(
            state="unknown",  # type: ignore[arg-type]
            reason_code="unknown",
            message="Unknown posture.",
        )
