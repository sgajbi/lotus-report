"""A tail-risk figure carries the basis that makes it interpretable (#235)."""

from app.reporting_render.package_builder import _risk_summary_section
from app.reporting_render.risk_methodology import build_risk_methodology

#: Shaped like what lotus-risk actually returns, not like what the package
#: already forwarded. The previous render fixture supplied only the five
#: forwarded fields, so it could not fail when the boundary dropped something.
CAPTURED = {
    "riskAnalytics": {
        "methodology": {"return_basis": "NET", "metrics": ["VOLATILITY", "DRAWDOWN", "VAR"]},
        "summary": {"YTD": {"volatility": 12.0, "drawdown": -8.0, "value_at_risk": -2.0}},
        "results": {
            "YTD": {
                "metrics": {
                    "DRAWDOWN": {"value": -8.0},
                    "VAR": {
                        "value": -2.0,
                        "details": {
                            "method": "HISTORICAL",
                            "confidence": 0.95,
                            "horizon_days": 1,
                            "horizon_scale_method": "SQRT_TIME",
                            "expected_shortfall": -3.1,
                        },
                    },
                }
            }
        },
    }
}


def test_a_tail_risk_number_carries_its_confidence_and_horizon():
    """ "Value at risk 2%" says nothing on its own: 2% over one day at 95% and
    2% over ten days at 99% are different statements about the same portfolio,
    and the page printed the bare number."""

    methodology = build_risk_methodology(CAPTURED)

    assert methodology["value_at_risk"] == {
        "method": "HISTORICAL",
        "confidence_pct": "95.00%",
        "horizon_days": "1",
        "horizon_scale_method": "SQRT_TIME",
    }
    assert methodology["return_basis"] == "NET"


def test_an_absent_basis_is_published_as_absent_rather_than_defaulted():
    """The canonical happy-path capture fixture carries no VaR `details` at
    all, which is exactly the case a default would silently paper over. Unlike
    a scalar there is no inferring a basis from the value, so a guess is
    indistinguishable from a fact."""

    methodology = build_risk_methodology({"riskAnalytics": {"summary": {"YTD": {}}}})

    assert methodology["return_basis"] is None
    assert methodology["value_at_risk"] == {
        "method": None,
        "confidence_pct": None,
        "horizon_days": None,
        "horizon_scale_method": None,
    }


def test_a_report_that_did_not_order_risk_has_no_basis_to_state():
    assert build_risk_methodology({}) == {}


def test_drawdown_and_expected_shortfall_reach_the_package():
    """Both were captured on every risk call and discarded here. The catalogue
    has advertised drawdown to callers all along."""

    summary = _risk_summary_section(CAPTURED, {"ytd_expected_shortfall_pct": -3.1})

    assert summary["drawdown_pct"] == "-8.00%"
    assert summary["expected_shortfall_pct"] == "-3.10%"


def test_a_portfolio_that_never_fell_is_not_reported_as_unmeasured():
    """A drawdown of exactly 0 is a finding - the portfolio did not fall during
    the period - not a gap. The `a or b` fallback this replaced read a
    legitimate zero as absence, which would have told a reader we could not
    measure the thing we measured."""

    snapshot = {"riskAnalytics": {"summary": {"YTD": {"drawdown": 0}}}}

    summary = _risk_summary_section(snapshot, {})

    assert summary["drawdown_pct"] == "0.00%"
