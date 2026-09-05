"""Source-cut coherence - one claim, independently defensible (report#283)."""

from __future__ import annotations

from app.reporting_identity import (
    SourceRevision,
    SourceRevisionVector,
    evaluate_source_cut_coherence,
)


def _vector(*revisions: SourceRevision) -> SourceRevisionVector:
    return SourceRevisionVector(revisions=tuple(revisions), coverage="partial")


def test_matching_stated_cuts_are_coherent() -> None:
    verdict = evaluate_source_cut_coherence(
        source_revisions=_vector(
            SourceRevision(source_service="lotus-core", as_of_date="2026-08-31"),
            SourceRevision(source_service="lotus-performance", as_of_date="2026-08-31"),
            SourceRevision(source_service="lotus-risk"),
        ),
        business_as_of_date="2026-08-31",
    )

    assert verdict.status == "coherent"
    assert verdict.policy_version == "scv1"


def test_a_differing_stated_cut_is_incoherent_and_named() -> None:
    """Offenders are NAMED, never averaged away - the detail is the
    operator's evidence."""

    verdict = evaluate_source_cut_coherence(
        source_revisions=_vector(
            SourceRevision(source_service="lotus-core", as_of_date="2026-08-31"),
            SourceRevision(source_service="lotus-performance", as_of_date="2026-08-30"),
        ),
        business_as_of_date="2026-08-31",
    )

    assert verdict.status == "incoherent"
    assert "lotus-performance=2026-08-30" in verdict.detail


def test_no_stated_cuts_are_unevaluable_never_coherent() -> None:
    """Absence of evidence is stated as absence: silence never grades as
    coherence."""

    verdict = evaluate_source_cut_coherence(
        source_revisions=_vector(
            SourceRevision(source_service="lotus-core"),
            SourceRevision(source_service="lotus-risk"),
        ),
        business_as_of_date="2026-08-31",
    )

    assert verdict.status == "unevaluable"
