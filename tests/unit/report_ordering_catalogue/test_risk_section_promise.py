"""The catalogue may not advertise an analytic the document cannot carry.

The ordering catalogue is Report's truth surface - it is what a caller reads
when deciding what to order. It advertised "concentration" for a section that
never called lotus-risk's concentration endpoint, and "drawdown" for a figure
captured on every risk call and discarded at the package boundary. A caller
ordering RISK_ANALYTICS for either got neither, and nothing said so.

That description was reviewed by humans repeatedly and read as true, which is
why this is asserted mechanically instead (issue #235).
"""

from app.report_ordering_catalogue.definitions import PORTFOLIO_REVIEW_SECTION_DEFINITIONS
from app.reporting_render.package_builder import _risk_summary_section

#: Analytic terms the description may use, and the render-package field that
#: actually delivers each one to the document.
DELIVERED_BY = {
    "portfolio risk": "volatility_pct",
    "drawdown": "drawdown_pct",
    "tail risk": "expected_shortfall_pct",
    "benchmark-relative": "beta",
}

#: Analytics lotus-risk can compute that Report does not carry to the
#: document. Naming one of these in the description promises a caller
#: something the section cannot deliver.
NOT_CARRIED = (
    "concentration",
    "attribution",
    "rolling",
    "scenario",
    "stress",
    "risk event",
)

FULLY_CAPTURED = {
    "riskAnalytics": {
        "summary": {
            "YTD": {
                "volatility": 12.0,
                "beta": 0.82,
                "tracking_error": 4.0,
                "information_ratio": 0.72,
                "value_at_risk": -2.0,
                "drawdown": -8.0,
            }
        },
    }
}


def _risk_description() -> str:
    for section in PORTFOLIO_REVIEW_SECTION_DEFINITIONS:
        if section.section_id == "RISK_ANALYTICS":
            return section.description.lower()
    raise AssertionError("RISK_ANALYTICS section is not in the catalogue")


def test_every_advertised_analytic_reaches_the_document():
    """A term in the description must correspond to a figure the package
    actually carries when the capture succeeded."""

    description = _risk_description()
    summary = _risk_summary_section(FULLY_CAPTURED, {"ytd_expected_shortfall_pct": -3.1})

    advertised = [term for term in DELIVERED_BY if term in description]
    assert advertised, "the description names no recognised analytic"

    for term in advertised:
        field = DELIVERED_BY[term]
        assert summary.get(field) not in (None, "Not available"), (
            f'the catalogue advertises "{term}" but the package does not carry {field}'
        )


def test_the_description_promises_nothing_the_section_does_not_carry():
    """The specific failure this replaces: "concentration" was advertised for
    a section that never calls lotus-risk's concentration endpoint."""

    description = _risk_description()

    promised_but_absent = [term for term in NOT_CARRIED if term in description]

    assert not promised_but_absent, (
        f"the catalogue promises {promised_but_absent}, which the document does not carry"
    )
