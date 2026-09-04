"""Identity v2 invariants (report#283) - each steering invariant pinned.

Same revision id -> same tenant, same semantic request, same source revision
vector, same snapshot facts. Restatement -> new revision. Pure rerender of
the same snapshot -> the SAME revision. Missing source evidence stays
missing.
"""

from __future__ import annotations

import pytest

from app.reporting_identity import (
    ReportSeriesKey,
    SourceRevision,
    SourceRevisionVector,
    derive_report_revision,
)


def _series(**overrides) -> ReportSeriesKey:
    payload = {
        "tenant_id": "tenant-sg",
        "report_family_id": "portfolio_review",
        "report_type": "portfolio_review",
        "portfolio_scope": {"portfolio_ids": ["PB_SG_002", "PB_SG_001"]},
        "as_of_date": "2026-08-31",
        "reporting_currency": "USD",
        "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
        "sections": ("RISK_ANALYTICS", "OVERVIEW"),
        "allocation_dimensions": ("sector", "currency"),
        "semantic_options": {"net_or_gross": "NET"},
    }
    payload.update(overrides)
    return ReportSeriesKey.model_validate(payload)


def _vector(**revision_overrides) -> SourceRevisionVector:
    revision = {
        "source_service": "lotus-core",
        "source_product": "PortfolioHoldings",
        "source_product_version": "v1",
        "as_of_date": "2026-08-31",
        "content_hash": "sha256:holdings",
        "restatement_version": "r1",
    }
    revision.update(revision_overrides)
    return SourceRevisionVector(
        revisions=(
            SourceRevision.model_validate(revision),
            SourceRevision(source_service="lotus-risk", calculation_run_id="run_9"),
        ),
        coverage="partial",
    )


def test_the_series_key_identifies_the_request_not_an_execution() -> None:
    """Order-insensitive collections canonicalise identically however the
    caller happened to order them - one logical request, one series."""

    reordered = _series(
        portfolio_scope={"portfolio_ids": ["PB_SG_001", "PB_SG_002"]},
        sections=("OVERVIEW", "RISK_ANALYTICS"),
        allocation_dimensions=("currency", "sector"),
    )

    assert _series().digest() == reordered.digest()


@pytest.mark.parametrize(
    "override",
    [
        {"tenant_id": "tenant-hk"},
        {"as_of_date": "2026-09-01"},
        {"reporting_currency": "SGD"},
        {"benchmark_code": None},
        {"sections": ("OVERVIEW",)},
        {"semantic_options": {"net_or_gross": "GROSS"}},
        {"portfolio_scope": {"portfolio_ids": ["PB_SG_001"]}},
    ],
)
def test_every_output_affecting_option_changes_the_series(override) -> None:
    assert _series().digest() != _series(**override).digest()


def test_same_facts_derive_the_same_revision_and_rerender_mints_nothing() -> None:
    first = derive_report_revision(
        series_key=_series(),
        source_revisions=_vector(),
        snapshot_hash="sha256:snapshot-1",
    )
    second = derive_report_revision(
        series_key=_series(),
        source_revisions=_vector(),
        snapshot_hash="sha256:snapshot-1",
    )

    assert first == second
    assert first.report_revision_id.startswith("rrv2_")


def test_a_restatement_produces_a_different_revision() -> None:
    original = derive_report_revision(
        series_key=_series(),
        source_revisions=_vector(restatement_version="r1"),
        snapshot_hash="sha256:snapshot-1",
    )
    restated = derive_report_revision(
        series_key=_series(),
        source_revisions=_vector(restatement_version="r2", content_hash="sha256:holdings-r2"),
        snapshot_hash="sha256:snapshot-2",
    )

    assert original.report_revision_id != restated.report_revision_id
    assert original.series_digest == restated.series_digest


def test_changed_snapshot_facts_alone_change_the_revision() -> None:
    base = derive_report_revision(
        series_key=_series(), source_revisions=_vector(), snapshot_hash="sha256:snapshot-1"
    )
    recaptured = derive_report_revision(
        series_key=_series(), source_revisions=_vector(), snapshot_hash="sha256:snapshot-2"
    )

    assert base.report_revision_id != recaptured.report_revision_id


def test_source_revision_order_never_changes_the_vector_digest() -> None:
    core = SourceRevision(source_service="lotus-core", content_hash="sha256:a")
    risk = SourceRevision(source_service="lotus-risk", calculation_run_id="run_9")

    forward = SourceRevisionVector(revisions=(core, risk), coverage="partial")
    backward = SourceRevisionVector(revisions=(risk, core), coverage="partial")

    assert forward.digest() == backward.digest()


def test_missing_source_evidence_stays_missing() -> None:
    """Absence is recorded as absence: the canonical form omits unstated
    fields rather than filling them, and coverage never upgrades itself."""

    sparse = SourceRevision(source_service="lotus-performance")

    assert sparse.canonical() == {"source_service": "lotus-performance"}
    assert SourceRevisionVector(revisions=(sparse,)).coverage == "unknown"


def test_coverage_participates_in_identity() -> None:
    stated = SourceRevisionVector(
        revisions=(SourceRevision(source_service="lotus-core"),), coverage="complete"
    )
    unknown = SourceRevisionVector(
        revisions=(SourceRevision(source_service="lotus-core"),), coverage="unknown"
    )

    assert stated.digest() != unknown.digest()


def test_a_revision_without_a_snapshot_hash_is_refused() -> None:
    with pytest.raises(ValueError, match="REPORT_REVISION_SNAPSHOT_HASH_REQUIRED"):
        derive_report_revision(series_key=_series(), source_revisions=_vector(), snapshot_hash="  ")


def test_ordered_semantic_option_lists_keep_their_order_in_identity() -> None:
    """An output-affecting ordered list (column order, ranking) must change
    the series when reordered - only declared sets normalise."""

    ordered = _series(semantic_options={"columns": ["market_value", "weight"]})
    reversed_columns = _series(semantic_options={"columns": ["weight", "market_value"]})

    assert ordered.digest() != reversed_columns.digest()


def test_revision_ties_on_partial_sort_keys_cannot_reorder_the_digest() -> None:
    """Two revisions identical in every casual sort field but differing in
    another identity field must digest identically regardless of caller
    order - the sort key is the complete canonical value."""

    first = SourceRevision(source_service="lotus-core", methodology_version="m1")
    second = SourceRevision(source_service="lotus-core", methodology_version="m2")

    forward = SourceRevisionVector(revisions=(first, second), coverage="partial")
    backward = SourceRevisionVector(revisions=(second, first), coverage="partial")

    assert forward.digest() == backward.digest()


def test_an_invented_coverage_state_is_refused() -> None:
    with pytest.raises(Exception, match="coverage"):
        SourceRevisionVector(revisions=(), coverage="compelete")
