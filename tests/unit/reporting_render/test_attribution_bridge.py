"""The bridge answers "why did we outperform" without inventing a number (#254)."""

from app.reporting_render.attribution_bridge import build_attribution_bridge


def _snapshot(status="present", **overrides):
    attribution = {
        "status": status,
        "request": {
            "calculation_id": "calc-1",
            "period": "YTD",
            "metric_basis": "NET",
            "benchmark_code": "BMK",
        },
        "model": "brinson_fachler",
        "linking": "carino",
        "supportability": {"status": "ready", "notes": []},
        "results_by_period": {
            "YTD": {
                "status": "partial",
                "reasons": [
                    {
                        "code": "off_benchmark_exposure",
                        "severity": "warning",
                        "message": "Portfolio holds groups absent from the benchmark.",
                    }
                ],
                "levels": [
                    {
                        "dimension": "asset_class",
                        "groups": [
                            {
                                "key": {"asset_class": "equity"},
                                "portfolio_weight_avg": 65.0,
                                "benchmark_weight_avg": 60.0,
                                "portfolio_return": 4.25,
                                "benchmark_return": 3.8,
                                "allocation": 0.24,
                                "selection": 0.15,
                                "interaction": 0.03,
                                "total_effect": 0.42,
                            },
                            {
                                "key": {"asset_class": "fixed_income"},
                                "allocation": -0.05,
                                "selection": 0.02,
                                "interaction": 0.0,
                                "total_effect": -0.03,
                            },
                        ],
                        "allocation_total_pct": 0.19,
                        "selection_total_pct": 0.17,
                        "interaction_total_pct": 0.03,
                        "total_effect_pct": 0.39,
                    }
                ],
                "reconciliation": {
                    "total_active_return": 0.42,
                    "sum_of_effects": 0.39,
                    "residual": 0.03,
                    "residual_materiality": {
                        "classification": "warning",
                        "treatment": "disclose",
                    },
                },
            }
        },
    }
    attribution.update(overrides)
    return {"attribution": attribution}


def test_the_bridge_reconciles_and_the_residual_is_never_absorbed():
    """Total -> named parts -> explicit residual -> reconciled sum. The parts
    deliberately do NOT sum to the total here (0.39 vs 0.42), and the bridge
    states the 0.03 residual with the source's own classification rather than
    rebalancing the parts to make the story tidy."""

    bridge = build_attribution_bridge(_snapshot())

    assert bridge["posture"] == "ready"
    assert bridge["reconciliation"] == {
        "total_active_return_pp": "0.42",
        "sum_of_effects_pp": "0.39",
        "residual_pp": "0.03",
        "residual_classification": "warning",
        "residual_treatment": "disclose",
    }


def test_totals_are_the_sources_authoritative_fields_not_a_sum_of_rows():
    """lotus-performance states that downstream systems must not infer totals
    by summing visible rows. The level totals here (0.19 allocation) differ
    from what summing the two rows would give (0.24 - 0.05 = 0.19 happens to
    match for allocation, but total_effect 0.39 vs rows' 0.39) - the point is
    the totals come from their own fields, proven by forwarding them verbatim."""

    bridge = build_attribution_bridge(_snapshot())

    assert bridge["totals"] == {
        "allocation_pp": "0.19",
        "selection_pp": "0.17",
        "interaction_pp": "0.03",
        "total_effect_pp": "0.39",
    }


def test_effect_rows_carry_the_hierarchy_slot_and_a_reader_label():
    """Each row states its grouping_dimension and level from day one, so
    deeper levels ride the same shape without a contract break. The label
    speaks the allocation page's vocabulary, not the source's lowercase key."""

    bridge = build_attribution_bridge(_snapshot())

    equity, fixed_income = bridge["effects"]
    assert equity["grouping_dimension"] == "asset_class"
    assert equity["level"] == 1
    assert equity["group_key"] == "equity"
    assert equity["group_label"] == "Equity"
    assert equity["allocation_pp"] == "0.24"
    assert fixed_income["group_label"] == "Fixed Income"
    assert fixed_income["total_effect_pp"] == "-0.03"


def test_a_pending_calculation_is_said_with_its_identity():
    """The async posture reaches the page as a statement, never a wait: the
    calculation exists upstream, and regenerating collects it."""

    bridge = build_attribution_bridge(
        _snapshot(
            status="pending",
            accepted={"calculation_id": "calc-1", "result_path": "/p"},
            supportability={
                "status": "pending",
                "notes": [
                    {
                        "code": "attribution_accepted_not_complete",
                        "severity": "warning",
                        "message": "Still computing; regenerate to collect.",
                    }
                ],
            },
        )
    )

    assert bridge["posture"] == "pending"
    assert bridge["calculation_id"] == "calc-1"
    assert bridge["notes"][0]["code"] == "attribution_accepted_not_complete"
    assert "effects" not in bridge


def test_an_unavailable_capture_is_said_not_drawn():
    bridge = build_attribution_bridge(
        _snapshot(
            status="unavailable",
            supportability={
                "status": "unavailable",
                "notes": [
                    {
                        "code": "attribution_execution_failed",
                        "severity": "warning",
                        "message": "The attribution execution failed at the source.",
                    }
                ],
            },
        )
    )

    assert bridge["posture"] == "unavailable"
    assert "effects" not in bridge
    assert bridge["notes"][0]["code"] == "attribution_execution_failed"


def test_a_present_capture_missing_the_presented_period_is_unavailable():
    """Present-but-not-for-this-period must not draw another period's bridge
    or an empty chart - said, not drawn."""

    snapshot = _snapshot()
    snapshot["attribution"]["results_by_period"] = {"1Y": {"levels": []}}

    bridge = build_attribution_bridge(snapshot)

    assert bridge["posture"] == "unavailable"


def test_period_reasons_travel_with_the_sources_own_prose():
    """The source composes reader-adequate messages for its per-period
    reasons (off-benchmark exposure); they are forwarded verbatim so Render
    draws prose Report supplied rather than inventing any."""

    bridge = build_attribution_bridge(_snapshot())

    assert bridge["period_status"] == "partial"
    assert bridge["period_notes"][0]["code"] == "off_benchmark_exposure"
    assert "absent from the benchmark" in bridge["period_notes"][0]["message"]


def test_a_report_that_did_not_order_attribution_promises_nothing():
    assert build_attribution_bridge({}) == {}
