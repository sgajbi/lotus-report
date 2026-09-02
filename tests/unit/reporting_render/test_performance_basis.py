"""The presented returns say what they mean (issue #243)."""

from app.reporting_render.package_builder import (
    PRESENTED_RETURN_BASIS,
    _performance_basis_section,
    _performance_periods,
    _performance_summary_table,
)

#: Net and gross deliberately differ, so a figure sourced from the wrong one is
#: visible rather than coincidentally equal.
BOTH_BASES = {
    "performance": {
        "summary": {
            "YTD": {
                "net_cumulative_return": 7.93,
                "gross_cumulative_return": 9.12,
                "net_annualized_return": 7.93,
                "gross_annualized_return": 9.12,
            }
        }
    }
}


def test_the_published_basis_matches_the_figures_actually_presented():
    """The basis and the source key live together so they cannot disagree.
    Publishing "NET" while reading a gross field would be a confident wrong
    statement about money, which is worse than saying nothing at all."""

    basis = _performance_basis_section()["return_basis"]
    periods = _performance_periods(BOTH_BASES)
    summary = _performance_summary_table(BOTH_BASES)

    assert basis == "NET"
    assert periods[0]["net_return_pct"] == "7.93%"
    assert summary[0]["net_return_pct"] == "7.93%"
    assert summary[0]["annualized_return_pct"] == "7.93%"
    # The gross figures exist in the capture and are not what was presented.
    assert "9.12%" not in str(periods) + str(summary)


def test_the_basis_is_not_taken_from_the_declared_methodology_constant():
    """`performance.methodology.performance_basis` is the static string
    NET_AND_GROSS_WHERE_AVAILABLE, set without reference to what was computed.
    Forwarding a declared posture as though it were measured is the specific
    wrong fix this guards against."""

    declared = {
        "performance": {
            "methodology": {"performance_basis": "NET_AND_GROSS_WHERE_AVAILABLE"},
            "summary": {"YTD": {"net_cumulative_return": 7.93}},
        }
    }

    assert _performance_basis_section()["return_basis"] == PRESENTED_RETURN_BASIS
    assert (
        _performance_basis_section()["return_basis"]
        != declared["performance"]["methodology"]["performance_basis"]
    )


def test_an_absent_return_is_absent_rather_than_falling_back_to_gross():
    """A capture with only gross figures presents nothing rather than
    silently showing a gross number under a net basis."""

    gross_only = {"performance": {"summary": {"YTD": {"gross_cumulative_return": 9.12}}}}

    periods = _performance_periods(gross_only)

    assert periods[0]["net_return_pct"] == "Not available"
