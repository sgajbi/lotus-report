"""Client-facing methodology and qualification statements for portfolio reviews."""

from app.reporting_lineage.allocation_qualification import allocation_statement
from app.services.performance_history import performance_history_statement


def review_disclosures(response: dict[str, object]) -> list[dict[str, object]]:
    disclosures: list[dict[str, object]] = [
        {
            "disclosure_id": "performance_methodology",
            "severity": "standard",
            "text": (
                "Performance figures use source-provided net and gross time-weighted "
                "returns where available; sub-year annualized returns are suppressed unless "
                "source support is explicit."
            ),
        },
        {
            "disclosure_id": "risk_methodology",
            "severity": "standard",
            "text": (
                "Risk figures are calculated from sourced net daily return history and "
                "should be reviewed with section supportability notes before client use."
            ),
        },
        {
            "disclosure_id": "reporting_view",
            "severity": "standard",
            "text": (
                "Holdings and transactions are reporting views for portfolio review and "
                "may differ from official custody statements."
            ),
        },
    ]
    allocation = response.get("allocation")
    if isinstance(allocation, dict) and isinstance(allocation.get("qualification"), dict):
        disclosures.append(
            {
                "disclosure_id": "allocation_valuation_qualification",
                "severity": "standard"
                if allocation["qualification"].get("client_publication_allowed")
                else "supportability",
                "text": allocation_statement(allocation["qualification"]),
            }
        )
    performance = response.get("performance")
    if isinstance(performance, dict):
        qualification = performance.get("history_qualification")
        if isinstance(qualification, dict):
            disclosures.append(
                {
                    "disclosure_id": "performance_history_qualification",
                    "severity": "standard"
                    if qualification.get("client_publication_allowed")
                    else "supportability",
                    "text": performance_history_statement(qualification),
                }
            )
    readiness = response.get("readiness")
    if not isinstance(readiness, dict) or readiness.get("status") != "ready":
        disclosures.append(
            {
                "disclosure_id": "partial_supportability",
                "severity": "supportability",
                "text": (
                    "One or more requested sections are partial or unavailable and require "
                    "advisor review before client presentation."
                ),
            }
        )
    return disclosures
