from app.routers.integration import _build_evidence_surface_supportability


def test_build_evidence_surface_supportability_reports_degraded_posture() -> None:
    supportability = _build_evidence_surface_supportability(
        features=[
            {
                "key": "lotus-report.reporting.portfolio_review.evidence_pack.v1",
                "enabled": True,
            },
            {
                "key": "lotus-report.reporting.portfolio_review.job_ledger.v1",
                "enabled": False,
            },
        ],
        workflows=[
            {"workflow_key": "portfolio_review_reporting", "enabled": True},
            {"workflow_key": "portfolio_review_report_job", "enabled": False},
        ],
    )

    assert supportability == {
        "state": "degraded",
        "reason": "evidence_surface_degraded",
        "freshness_bucket": "unknown",
        "evidence_feature_count": 2,
        "ready_evidence_feature_count": 1,
        "degraded_evidence_feature_count": 1,
        "workflow_count": 2,
        "ready_workflow_count": 1,
    }


def test_build_evidence_surface_supportability_reports_empty_posture() -> None:
    supportability = _build_evidence_surface_supportability(
        features=[{"key": "lotus-report.reporting.portfolio_summary", "enabled": True}],
        workflows=[],
    )

    assert supportability == {
        "state": "empty",
        "reason": "evidence_surface_empty",
        "freshness_bucket": "unknown",
        "evidence_feature_count": 0,
        "ready_evidence_feature_count": 0,
        "degraded_evidence_feature_count": 0,
        "workflow_count": 0,
        "ready_workflow_count": 0,
    }
