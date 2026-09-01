"""A risk figure that is missing must say why (issue #234)."""

from app.reporting_render.risk_posture import (
    REASON_SUPPORTABILITY_UNSTATED,
    build_risk_posture,
)


def _snapshot(status, notes=None):
    supportability = {"status": status}
    if notes is not None:
        supportability["notes"] = notes
    return {"riskAnalytics": {"supportability": supportability}}


def test_a_report_that_did_not_order_risk_has_no_posture_to_state():
    """Nothing was promised, so there is nothing to explain - the same reason
    the panel itself is not drawn for an unordered section."""

    assert build_risk_posture({}) == {}


def test_each_stated_posture_is_forwarded_verbatim():
    """A vocabulary tested at one value is a vocabulary tested nowhere."""

    for status in ("ready", "partial", "unavailable"):
        assert build_risk_posture(_snapshot(status, []))["posture"] == status


def test_a_healthy_section_states_ready_with_nothing_to_explain():
    """An empty note list must stay empty. Render draws no explanatory line
    then - an absence of notes is not a reassurance the page has earned."""

    posture = build_risk_posture(_snapshot("ready", []))

    assert posture == {"posture": "ready", "notes": []}


def test_a_missing_benchmark_and_an_upstream_failure_stay_distinguishable():
    """The whole point. Both print "Not available" on the page today, and they
    point an operator in opposite directions: no benchmark is permanent and
    expected for this mandate, an upstream failure is transient and worth
    re-running the report for."""

    mandate_fact = build_risk_posture(
        _snapshot(
            "partial",
            [{"code": "missing_benchmark", "severity": "informational", "message": "m"}],
        )
    )
    data_fact = build_risk_posture(
        _snapshot(
            "unavailable",
            [{"code": "risk_upstream_failure", "severity": "blocking", "message": "m"}],
        )
    )

    assert mandate_fact["notes"][0]["code"] == "missing_benchmark"
    assert data_fact["notes"][0]["code"] == "risk_upstream_failure"
    assert mandate_fact["posture"] != data_fact["posture"]


def test_an_unstated_status_is_not_read_as_health():
    """A status the capture failed to establish is not a clean bill of health.
    Defaulting to `ready` would publish exactly the unearned reassurance this
    module exists to remove."""

    posture = build_risk_posture(_snapshot("", []))

    assert posture["posture"] == "unavailable"
    assert posture["notes"][0]["code"] == REASON_SUPPORTABILITY_UNSTATED

    unrecognised = build_risk_posture(_snapshot("mostly_fine", []))
    assert unrecognised["posture"] == "unavailable"


def test_an_unrecognised_note_code_is_kept_rather_than_guessed():
    """Note codes are forwarded verbatim while the posture is bounded, because
    the code identifies the fault for an operator and does not drive a branch.
    Replacing an unfamiliar code with a guess would erase the one field that
    says what actually happened."""

    posture = build_risk_posture(
        _snapshot("partial", [{"code": "some_future_code", "severity": "warning"}])
    )

    assert posture["notes"][0]["code"] == "some_future_code"
    assert posture["notes"][0]["severity"] == "warning"
    assert posture["notes"][0]["message"] is None


def test_a_period_scoped_note_says_which_period():
    """A section-wide fault must never read as a per-period one, so `period`
    is published only when the note carries it."""

    scoped = build_risk_posture(
        _snapshot(
            "partial",
            [{"code": "risk_period_upstream_failure", "severity": "warning", "period": "1Y"}],
        )
    )
    unscoped = build_risk_posture(
        _snapshot("partial", [{"code": "missing_benchmark", "severity": "informational"}])
    )

    assert scoped["notes"][0]["period"] == "1Y"
    assert "period" not in unscoped["notes"][0]


def test_malformed_notes_are_dropped_rather_than_published_as_empty():
    """A note that is not a mapping carries nothing an operator can act on."""

    posture = build_risk_posture(_snapshot("partial", ["not-a-note", None, 42]))

    assert posture["notes"] == []


def test_a_benchmark_note_names_the_measures_it_is_about():
    """`missing_benchmark` is ONE fact about the mandate covering three
    measures, so the page states it once and names them - three separate
    markers would invite a reader to think three separate things went wrong.

    Report supplies the list so the renderer does not keep its own copy of
    which measures are benchmark-relative. That copy would go stale the moment
    the requested metric set moved, and nothing would catch it - the same
    two-copies defect as the availability/capture reason mapping.
    """

    posture = build_risk_posture(
        _snapshot(
            "partial",
            [
                {
                    "code": "missing_benchmark",
                    "severity": "informational",
                    "metrics": ["BETA", "TRACKING_ERROR", "INFORMATION_RATIO"],
                }
            ],
        )
    )

    assert posture["notes"][0]["affected_measures"] == [
        "beta",
        "tracking_error_pct",
        "information_ratio",
    ]


def test_a_note_about_a_measure_the_page_does_not_show_says_so():
    """`missing_risk_free_rate` concerns Sharpe, which this report captures and
    does not present. An empty list is a real answer and distinct from absent:
    there is nothing on the page to say the sentence about, so the consumer can
    keep it off rather than drawing a note about an invisible measure."""

    posture = build_risk_posture(
        _snapshot(
            "partial",
            [
                {
                    "code": "missing_risk_free_rate",
                    "severity": "informational",
                    "metrics": ["SHARPE"],
                }
            ],
        )
    )

    assert posture["notes"][0]["affected_measures"] == []


def test_a_note_the_capture_did_not_scope_stays_unscoped():
    """A section-wide fault must not acquire a measure list it never had -
    absent means "about the section", and an empty list would wrongly say
    "about nothing on this page"."""

    posture = build_risk_posture(
        _snapshot("unavailable", [{"code": "risk_upstream_failure", "severity": "blocking"}])
    )

    assert "affected_measures" not in posture["notes"][0]


def test_the_benchmark_measures_come_from_the_constant_that_requests_them():
    """The list Report publishes must be derived from the same constant that
    decides those metrics are only requested with a benchmark, not written out
    a second time beside it."""

    from app.services.reporting_read_service import BENCHMARK_RISK_METRICS

    posture = build_risk_posture(
        _snapshot(
            "partial",
            [
                {
                    "code": "missing_benchmark",
                    "severity": "informational",
                    "metrics": list(BENCHMARK_RISK_METRICS),
                }
            ],
        )
    )

    assert len(posture["notes"][0]["affected_measures"]) == len(BENCHMARK_RISK_METRICS)
