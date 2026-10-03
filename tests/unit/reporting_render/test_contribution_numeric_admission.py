"""Contribution evidence admission through native mapping and package construction."""

import copy
import json
from decimal import Decimal

import pytest

from app.reporting_render.package_builder import _build_render_package
from app.services.reporting_read_service import ReportingReadService
from tests.unit.reporting_render.test_rerender_eligibility import _job

BAD_VALUES = [
    "NaN",
    "sNaN",
    "Infinity",
    "-Infinity",
    float("nan"),
    float("inf"),
    Decimal("NaN"),
    Decimal("sNaN"),
    Decimal("Infinity"),
    True,
    None,
    "invalid",
]


def _source(value="1.25"):
    return {
        "results_by_period": {
            "YTD": {
                "total_portfolio_return": "1.50",
                "total_contribution": "1.50",
                "position_contributions": [
                    {
                        "position_id": "synthetic:SEC_A",
                        "total_contribution": value,
                        "average_weight": "50",
                        "total_return": "2.50",
                        "local_contribution": "1",
                        "fx_contribution": "0.25",
                    },
                    {"position_id": "synthetic:SEC_B", "total_contribution": "0.25"},
                ],
                "levels": [
                    {
                        "level": 1,
                        "rows": [
                            {
                                "key": {"asset_class": "Equity"},
                                "contribution": "1.5",
                                "weight_avg": "100",
                            }
                        ],
                    }
                ],
            }
        }
    }


def _package(source):
    source_before = copy.deepcopy(source)
    service = ReportingReadService(
        core_query_client=object(), performance_client=object(), risk_client=object()
    )
    mapped = service._map_performance_contribution(status_code=200, payload=source)
    assert source == source_before
    performance = {"contribution": mapped}
    snapshot = {
        "performance": performance,
        "keyFigures": {"performance": service._performance_key_figures(performance)},
    }
    before = copy.deepcopy(snapshot)
    package = _build_render_package(
        job=_job(
            render_template_id="portfolio-review",
            render_template_version="v1",
            options={"sections": ["PERFORMANCE"]},
        ),
        snapshot=snapshot,
        render_job_id="synthetic-render",
        snapshot_id="synthetic-snapshot",
    )
    assert snapshot == before
    json.dumps(package, allow_nan=False)
    return mapped, package["report_data"]["contribution_ranking"]


@pytest.mark.parametrize("value", BAD_VALUES)
def test_invalid_required_contribution_keeps_identity_and_counts_the_unusable_row(value):
    mapped, ranking = _package(_source(value))
    assert mapped["top_position_contributors"][0]["security_id"] == "SEC_A"
    assert mapped["top_position_contributors"][0]["total_contribution_pct"] is None
    assert ranking["posture"] == "ready"
    assert ranking["available_count"] == ranking["presented_count"] == 1
    assert ranking["unusable_row_count"] == 1
    assert ranking["presented_contribution_pct"] == "0.25"
    assert ranking["contributors"][0]["name"] == "SEC_B"


@pytest.mark.parametrize(
    "field,published",
    [
        ("average_weight", "average_weight_pct"),
        ("total_return", "return_pct"),
        ("local_contribution", None),
        ("fx_contribution", None),
    ],
)
@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", True])
def test_invalid_optional_values_do_not_discard_a_finite_contributor(field, published, value):
    source = _source()
    source["results_by_period"]["YTD"]["position_contributions"][0][field] = value
    mapped, ranking = _package(source)
    mapped_field = {
        "average_weight": "average_weight_pct",
        "total_return": "total_return_pct",
        "local_contribution": "local_contribution_pct",
        "fx_contribution": "fx_contribution_pct",
    }[field]
    assert mapped["top_position_contributors"][0][mapped_field] is None
    assert ranking["posture"] == "ready" and ranking["unusable_row_count"] == 0
    assert ranking["presented_contribution_pct"] == "1.50"
    if published:
        assert ranking["contributors"][0][published] is None


@pytest.mark.parametrize(
    "field,published",
    [
        ("total_portfolio_return", "total_portfolio_return_pct"),
        ("total_contribution", "explained_contribution_pct"),
    ],
)
@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", True])
def test_invalid_source_totals_are_unavailable_and_are_not_reconstructed(field, published, value):
    source = _source()
    source["results_by_period"]["YTD"][field] = value
    mapped, ranking = _package(source)
    mapped_field = (
        "total_portfolio_return_pct"
        if field == "total_portfolio_return"
        else "total_contribution_pct"
    )
    assert mapped[mapped_field] is None
    assert ranking[published] is None and ranking["unexplained_residual_pct"] is None
    assert ranking["presented_contribution_pct"] == "1.50"


@pytest.mark.parametrize(
    "field,published", [("contribution", "contribution_pct"), ("weight_avg", "average_weight_pct")]
)
def test_hierarchy_values_obey_the_same_finite_admission(field, published):
    source = _source()
    source["results_by_period"]["YTD"]["levels"][0]["rows"][0][field] = "Infinity"
    mapped, _ranking = _package(source)
    assert mapped["hierarchy"][0]["rows"][0][published] is None


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", True, None])
def test_all_unusable_contributors_remain_distinct_from_genuine_empty_activity(value):
    source = _source(value)
    source["results_by_period"]["YTD"]["position_contributions"] = source["results_by_period"][
        "YTD"
    ]["position_contributions"][:1]
    _mapped, unavailable = _package(source)
    assert unavailable["posture"] == "unavailable" and unavailable["unusable_row_count"] == 1
    source["results_by_period"]["YTD"]["position_contributions"] = []
    _mapped, empty = _package(source)
    assert empty["posture"] == "empty" and "unusable_row_count" not in empty


@pytest.mark.parametrize(
    "value,formatted,total",
    [
        ("1.25", "1.25", "1.50"),
        ("-1.25", "-1.25", "-1.00"),
        ("0", "0.00", "0.25"),
        (Decimal("1.25"), "1.25", "1.50"),
        (1.25, "1.25", "1.50"),
    ],
)
def test_finite_source_representations_zero_and_both_signs_remain_usable(value, formatted, total):
    mapped, ranking = _package(_source(value))
    assert mapped["top_position_contributors"][0]["total_contribution_pct"] == value
    assert ranking["unusable_row_count"] == 0
    assert ranking["presented_contribution_pct"] == total
    assert formatted in [row["contribution_pct"] for row in ranking["contributors"]]
    assert ranking["unexplained_residual_pct"] == "0.00"


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", True])
def test_retained_contribution_fields_are_withheld_without_changing_snapshot_bytes(value):
    snapshot = {
        "performance": {
            "contribution": {
                "status": "present",
                "period": "YTD",
                "total_portfolio_return_pct": value,
                "total_contribution_pct": value,
                "top_position_contributors": [
                    {"security_id": "SEC_A", "total_contribution_pct": value},
                    {
                        "security_id": "SEC_B",
                        "total_contribution_pct": "0.25",
                        "average_weight_pct": value,
                        "total_return_pct": value,
                    },
                ],
            }
        },
        "keyFigures": {
            "performance": {
                "largest_positive_contributor": {
                    "security_id": "SEC_A",
                    "total_contribution_pct": value,
                }
            }
        },
        "holdings": {
            "holdingsByAssetClass": {
                "Equity": [
                    {
                        "security_id": "SEC_A",
                        "market_value": "10",
                        "weight": "1",
                        "ytd_contribution_pct": value,
                    }
                ]
            }
        },
    }
    before = json.dumps(snapshot, sort_keys=True)
    package = _build_render_package(
        job=_job(
            render_template_id="portfolio-review",
            render_template_version="v1",
            options={"sections": ["PERFORMANCE"]},
        ),
        snapshot=snapshot,
        render_job_id="synthetic-render",
        snapshot_id="synthetic-snapshot",
    )
    assert json.dumps(snapshot, sort_keys=True) == before
    data = package["report_data"]
    ranking = data["contribution_ranking"]
    assert ranking["unusable_row_count"] == 1 and ranking["presented_contribution_pct"] == "0.25"
    assert ranking["total_portfolio_return_pct"] is None
    assert ranking["explained_contribution_pct"] is None
    assert ranking["unexplained_residual_pct"] is None
    assert ranking["contributors"][0]["average_weight_pct"] is None
    assert ranking["contributors"][0]["return_pct"] is None
    assert data["performance_highlight"]["largest_positive_contribution_pct"] == "Not available"
    assert data["performance_highlight"]["largest_positive_contributor_name"] == "Not available"
    assert data["positions"][0]["ytd_contribution_pct"] == "Not available"
    assert not any("SEC_A was the largest" in text for text in data["review_observations"])


def test_extreme_selection_uses_finite_decimal_precision_and_rejects_nonfinite_and_bool():
    service = ReportingReadService(
        core_query_client=object(), performance_client=object(), risk_client=object()
    )
    rows = [
        {"security_id": "SEC_A", "total_contribution_pct": "1.00000000000000001"},
        {
            "security_id": "SEC_Z",
            "total_contribution_pct": "1.00000000000000002",
            "average_weight_pct": "NaN",
            "total_return_pct": True,
        },
        {"security_id": "SEC_BAD", "total_contribution_pct": "Infinity"},
        {"security_id": "SEC_BOOL", "total_contribution_pct": True},
    ]
    selected = service._contribution_extreme(rows, largest=True)
    assert selected["security_id"] == "SEC_Z"
    assert selected["average_weight_pct"] is selected["total_return_pct"] is None
    assert service._contribution_extreme(rows[:1], largest=False)["security_id"] == "SEC_A"
    assert service._contribution_extreme(rows[-2:], largest=True) is None


@pytest.mark.parametrize(
    "values,positive,negative",
    [
        (["NaN", "-0.25"], None, "-0.25"),
        (["Infinity", "0.25"], "0.25", None),
        (["sNaN", "0"], None, None),
        (["-0.5", "0", "1.25", "-2", "0.25"], "1.25", "-2"),
    ],
)
def test_sign_labelled_key_figures_select_only_eligible_finite_contributors(
    values, positive, negative
):
    service = ReportingReadService(
        core_query_client=object(), performance_client=object(), risk_client=object()
    )
    source = _source()
    source["results_by_period"]["YTD"]["position_contributions"] = [
        {"position_id": f"synthetic:SEC_{index}", "total_contribution": value}
        for index, value in enumerate(values)
    ]
    mapped, ranking = _package(source)
    figures = service._performance_key_figures({"contribution": mapped})
    for label, expected in [("positive", positive), ("negative", negative)]:
        selected = figures[f"largest_{label}_contributor"]
        if expected is None:
            assert selected is None
        else:
            assert selected["total_contribution_pct"] == expected
    assert ranking["available_count"] == len(values) - sum(
        value in {"NaN", "sNaN", "Infinity"} for value in values
    )


@pytest.mark.parametrize("field", ["ytd_average_weight_pct", "ytd_total_return_pct"])
@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", True])
def test_retained_optional_holding_contribution_values_are_withheld_with_finite_neighbors(
    field, value
):
    snapshot = {
        "holdings": {
            "holdingsByAssetClass": {
                "Equity": [
                    {"security_id": "SEC_BAD", field: value},
                    {"security_id": "SEC_FINITE", field: "-1.25"},
                    {"security_id": "SEC_ZERO", field: "0"},
                ]
            }
        }
    }
    before = json.dumps(snapshot, sort_keys=True)
    package = _build_render_package(
        job=_job(
            render_template_id="portfolio-review",
            render_template_version="v1",
            options={"sections": ["PERFORMANCE"]},
        ),
        snapshot=snapshot,
        render_job_id="synthetic-render",
        snapshot_id="synthetic-snapshot",
    )
    assert json.dumps(snapshot, sort_keys=True) == before
    json.dumps(package, allow_nan=False)
    values = {row["security_id"]: row[field] for row in package["report_data"]["positions"]}
    assert values == {"SEC_BAD": "Not available", "SEC_FINITE": "-1.25%", "SEC_ZERO": "0.00%"}


@pytest.mark.parametrize("value", [0, "0", Decimal("0")])
def test_zero_highlight_remains_zero_and_does_not_fall_through_to_another_field(value):
    snapshot = {
        "keyFigures": {
            "performance": {
                "largest_positive_contributor": {
                    "security_id": "SEC_ZERO",
                    "total_contribution_pct": value,
                    "ytd_contribution_pct": "99",
                }
            }
        }
    }
    package = _build_render_package(
        job=_job(
            render_template_id="portfolio-review",
            render_template_version="v1",
            options={"sections": ["PERFORMANCE"]},
        ),
        snapshot=snapshot,
        render_job_id="synthetic-render",
        snapshot_id="synthetic-snapshot",
    )
    assert (
        package["report_data"]["performance_highlight"]["largest_positive_contribution_pct"]
        == "0.00%"
    )


def test_explicit_unknown_current_highlight_does_not_use_a_legacy_fallback():
    snapshot = {
        "keyFigures": {
            "performance": {
                "largest_positive_contributor": {
                    "security_id": "SEC_UNKNOWN",
                    "total_contribution_pct": None,
                    "ytd_contribution_pct": "99",
                }
            }
        }
    }
    package = _build_render_package(
        job=_job(
            render_template_id="portfolio-review",
            render_template_version="v1",
            options={"sections": ["PERFORMANCE"]},
        ),
        snapshot=snapshot,
        render_job_id="synthetic-render",
        snapshot_id="synthetic-snapshot",
    )
    assert (
        package["report_data"]["performance_highlight"]["largest_positive_contribution_pct"]
        == "Not available"
    )


@pytest.mark.parametrize("field", ["total_contribution_pct", "ytd_contribution_pct"])
@pytest.mark.parametrize(
    "value,formatted,name",
    [
        ("-0.25", "Not available", "Not available"),
        ("0", "0.00%", "Not available"),
        ("0.25", "0.25%", "SEC_RETAINED"),
    ],
)
def test_retained_positive_highlight_name_and_observation_require_positive_evidence(
    field, value, formatted, name
):
    snapshot = {
        "keyFigures": {
            "performance": {
                "largest_positive_contributor": {"security_id": "SEC_RETAINED", field: value}
            }
        }
    }
    before = json.dumps(snapshot, sort_keys=True)
    package = _build_render_package(
        job=_job(
            render_template_id="portfolio-review",
            render_template_version="v1",
            options={"sections": ["PERFORMANCE"]},
        ),
        snapshot=snapshot,
        render_job_id="synthetic-render",
        snapshot_id="synthetic-snapshot",
    )
    assert json.dumps(snapshot, sort_keys=True) == before
    data = package["report_data"]
    assert data["performance_highlight"]["largest_positive_contribution_pct"] == formatted
    assert data["performance_highlight"]["largest_positive_contributor_name"] == name
    assert any(
        "SEC_RETAINED was the largest positive" in text for text in data["review_observations"]
    ) == (value == "0.25")


def test_finite_large_contribution_can_be_formatted_without_decimal_context_overflow():
    mapped, ranking = _package(_source("1E+30"))
    assert mapped["top_position_contributors"][0]["total_contribution_pct"] == "1E+30"
    assert ranking["posture"] == "ready" and ranking["unusable_row_count"] == 0
    assert ranking["contributors"][0]["contribution_pct"] == "1000000000000000000000000000000.00"


def test_magnitude_ranking_preserves_decimal_precision_beyond_the_arithmetic_context():
    snapshot = {
        "performance": {
            "contribution": {
                "status": "present",
                "period": "YTD",
                "top_position_contributors": [
                    {
                        "security_id": "SEC_A",
                        "total_contribution_pct": "1.0000000000000000000000000000000001",
                    },
                    {
                        "security_id": "SEC_Z",
                        "total_contribution_pct": "1.0000000000000000000000000000000002",
                    },
                ],
            }
        }
    }
    package = _build_render_package(
        job=_job(
            render_template_id="portfolio-review",
            render_template_version="v1",
            options={"sections": ["PERFORMANCE"]},
        ),
        snapshot=snapshot,
        render_job_id="synthetic-render",
        snapshot_id="synthetic-snapshot",
    )
    assert [
        row["name"] for row in package["report_data"]["contribution_ranking"]["contributors"]
    ] == ["SEC_Z", "SEC_A"]
