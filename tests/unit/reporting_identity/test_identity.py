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
    factual_content_digest,
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
        factual_content_digest="sha256:facts-1",
    )
    second = derive_report_revision(
        series_key=_series(),
        source_revisions=_vector(),
        factual_content_digest="sha256:facts-1",
    )

    assert first == second
    assert first.report_revision_id.startswith("rrv2_")


def test_a_restatement_produces_a_different_revision() -> None:
    original = derive_report_revision(
        series_key=_series(),
        source_revisions=_vector(restatement_version="r1"),
        factual_content_digest="sha256:facts-1",
    )
    restated = derive_report_revision(
        series_key=_series(),
        source_revisions=_vector(restatement_version="r2", content_hash="sha256:holdings-r2"),
        factual_content_digest="sha256:facts-2",
    )

    assert original.report_revision_id != restated.report_revision_id
    assert original.series_digest == restated.series_digest


def test_changed_snapshot_facts_alone_change_the_revision() -> None:
    base = derive_report_revision(
        series_key=_series(), source_revisions=_vector(), factual_content_digest="sha256:facts-1"
    )
    recaptured = derive_report_revision(
        series_key=_series(), source_revisions=_vector(), factual_content_digest="sha256:facts-2"
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
    evidenced = SourceRevision(source_service="lotus-core", content_hash="sha256:a")
    stated = SourceRevisionVector(revisions=(evidenced,), coverage="complete")
    unknown = SourceRevisionVector(revisions=(evidenced,), coverage="unknown")

    assert stated.digest() != unknown.digest()


def test_a_revision_without_a_content_digest_is_refused() -> None:
    with pytest.raises(ValueError, match="REPORT_REVISION_CONTENT_DIGEST_REQUIRED"):
        derive_report_revision(
            series_key=_series(), source_revisions=_vector(), factual_content_digest="  "
        )


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


@pytest.mark.parametrize(
    "build",
    [
        lambda: ReportSeriesKey.model_validate(
            {
                "tenant_id": "tenant-sg",
                "report_family_id": "portfolio_review",
                "report_type": "portfolio_review",
                "portfolio_scope": {"portfolio_ids": ["P1"]},
                "as_of_date": "2026-08-31",
                "fee_basis": "gross",
            }
        ),
        lambda: SourceRevisionVector.model_validate(
            {"revisions": [], "coverage": "unknown", "cut_token": "x"}
        ),
        lambda: __import__(
            "app.reporting_identity", fromlist=["ReportRevisionIdentity"]
        ).ReportRevisionIdentity.model_validate(
            {
                "report_revision_id": "rrv2_x",
                "series_digest": "a",
                "source_revision_digest": "b",
                "factual_content_digest": "c",
                "factual_boundary_version": "fb1",
                "extra_field": "y",
            }
        ),
    ],
    ids=["series", "vector", "derived-identity"],
)
def test_unknown_identity_fields_fail_validation(build) -> None:
    """Fail-closed: a producer introducing a new output-affecting field
    before this consumer models it must break validation - never keep the
    old identity by silent discard."""

    with pytest.raises(Exception, match="[Ee]xtra|forbid|permitted"):
        build()


def test_caller_side_mutation_cannot_change_an_admitted_identity() -> None:
    scope = {"portfolio_ids": ["PB_SG_001"]}
    series = _series(portfolio_scope=scope)
    before = series.digest()

    scope["portfolio_ids"].append("PB_SG_999")

    assert series.digest() == before


def test_complete_coverage_must_be_backed_by_evidence() -> None:
    with pytest.raises(Exception, match="SOURCE_REVISION_COVERAGE_UNBACKED"):
        SourceRevisionVector(revisions=(), coverage="complete")
    with pytest.raises(Exception, match="SOURCE_REVISION_COVERAGE_UNBACKED"):
        SourceRevisionVector(
            revisions=(SourceRevision(source_service="lotus-core"),), coverage="complete"
        )


def test_from_evidence_computes_coverage_instead_of_trusting_a_label() -> None:
    stated = SourceRevision(source_service="lotus-core", content_hash="sha256:a")
    silent = SourceRevision(source_service="lotus-risk")

    complete = SourceRevisionVector.from_evidence(
        revisions=(stated,), expected_sources=("lotus-core",)
    )
    partial = SourceRevisionVector.from_evidence(
        revisions=(stated, silent), expected_sources=("lotus-core", "lotus-risk")
    )
    unknown = SourceRevisionVector.from_evidence(
        revisions=(silent,), expected_sources=("lotus-risk",)
    )

    assert complete.coverage == "complete"
    assert partial.coverage == "partial"
    assert unknown.coverage == "unknown"


def test_blank_revision_fields_are_not_evidence() -> None:
    """A source supplying "" or whitespace has stated nothing: the canonical
    form omits it and coverage cannot claim complete over blanks."""

    blank = SourceRevision(source_service="lotus-core", content_hash="   ")

    assert blank.canonical() == {"source_service": "lotus-core"}
    vector = SourceRevisionVector.from_evidence(
        revisions=(blank,), expected_sources=("lotus-core",)
    )
    assert vector.coverage == "unknown"


def test_capture_instance_fields_are_outside_the_factual_boundary() -> None:
    """Timestamps and transport metadata at ANY depth are capture-instance
    facts, not report facts: removing or changing them never changes the
    factual digest."""

    payload = {
        "portfolio_id": "P1",
        "generated_at": "2026-04-22T09:00:01Z",
        "correlation_id": "corr-a",
        "evidence": {
            "trust_metadata": {
                "generated_at": "2026-04-22T09:00:01Z",
                "correlation_id": "corr-a",
                "trace_id": "trace-a",
            }
        },
        "rows": [{"value": "1.25", "captured_at": "2026-04-22T09:00:00Z"}],
    }
    recaptured = {
        "portfolio_id": "P1",
        "generated_at": "2026-04-23T11:30:00Z",
        "correlation_id": "corr-b",
        "evidence": {
            "trust_metadata": {
                "generated_at": "2026-04-23T11:30:00Z",
                "correlation_id": "corr-b",
                "trace_id": "trace-b",
            }
        },
        "rows": [{"value": "1.25", "captured_at": "2026-04-23T11:29:59Z"}],
    }

    assert factual_content_digest(payload) == factual_content_digest(recaptured)


def test_a_changed_fact_changes_the_factual_digest() -> None:
    base = {"portfolio_id": "P1", "rows": [{"value": "1.25"}], "generated_at": "t1"}
    changed = {"portfolio_id": "P1", "rows": [{"value": "1.26"}], "generated_at": "t1"}

    assert factual_content_digest(base) != factual_content_digest(changed)


def test_the_digest_refuses_a_payload_carrying_its_own_revision_id() -> None:
    """No circular identity: the revision binding lives beside the payload,
    never inside its own preimage."""

    with pytest.raises(ValueError, match="REPORT_REVISION_CIRCULAR_IDENTITY"):
        factual_content_digest({"report_revision_id": "rrv2_x", "portfolio_id": "P1"})


def test_catalogue_identity_alone_never_establishes_coverage() -> None:
    """Product name and version say WHICH PRODUCT served - never which data
    revision supplied the facts."""

    catalogue_only = SourceRevision(
        source_service="lotus-core",
        source_product="HoldingsAsOf",
        source_product_version="v1",
    )
    vector = SourceRevisionVector.from_evidence(
        revisions=(catalogue_only,), expected_sources=("lotus-core",)
    )

    assert vector.coverage == "unknown"
    assert not catalogue_only.states_revision_evidence()


def test_quality_labels_alone_never_establish_coverage() -> None:
    """Supportability and reconciliation labels grade the data - they do not
    identify its revision. Neither do request semantics or configuration."""

    quality_only = SourceRevision(
        source_service="lotus-core",
        as_of_date="2026-08-31",
        methodology_version="m1",
        supportability_status="complete",
        reconciliation_state="reconciled",
    )
    vector = SourceRevisionVector.from_evidence(
        revisions=(quality_only,), expected_sources=("lotus-core",)
    )

    assert vector.coverage == "unknown"
    assert not quality_only.states_revision_evidence()


def test_a_complete_claim_backed_only_by_catalogue_identity_is_refused() -> None:
    with pytest.raises(ValueError, match="SOURCE_REVISION_COVERAGE_UNBACKED"):
        SourceRevisionVector(
            revisions=(
                SourceRevision(
                    source_service="lotus-core",
                    source_product="HoldingsAsOf",
                    source_product_version="v1",
                ),
            ),
            coverage="complete",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content_hash", "sha256:holdings-r1"),
        ("source_snapshot_id", "core-snap-9"),
        ("restatement_version", "r1"),
        ("source_batch_fingerprint", "core-batch-77"),
        ("calculation_run_id", "run_accept_1"),
        ("generated_at", "2026-08-31T08:59:59Z"),
    ],
)
def test_each_qualifying_field_establishes_stated_revision_evidence(field, value) -> None:
    revision = SourceRevision(source_service="lotus-core", **{field: value})
    vector = SourceRevisionVector.from_evidence(
        revisions=(revision,), expected_sources=("lotus-core",)
    )

    assert revision.states_revision_evidence()
    assert vector.coverage == "complete"


def test_mixed_sources_with_one_catalogue_only_participant_stay_partial() -> None:
    """One source stating a real revision plus one stating only catalogue
    identity is PARTIAL coverage - the catalogue-only participant remains
    unevidenced even though it stated fields."""

    evidenced = SourceRevision(source_service="lotus-core", content_hash="sha256:a")
    catalogue_only = SourceRevision(
        source_service="lotus-performance",
        source_product="WorkspaceSummary",
        source_product_version="v1",
    )
    vector = SourceRevisionVector.from_evidence(
        revisions=(evidenced, catalogue_only),
        expected_sources=("lotus-core", "lotus-performance"),
    )

    assert vector.coverage == "partial"


def test_a_catalogue_only_row_beside_a_qualifying_row_of_the_same_source_is_valid() -> None:
    """Coverage is a PER-SOURCE claim: a source that stated qualifying
    evidence in one product block may state only catalogue identity in
    another without invalidating the complete claim."""

    vector = SourceRevisionVector(
        revisions=(
            SourceRevision(source_service="lotus-core", content_hash="sha256:a"),
            SourceRevision(
                source_service="lotus-core",
                source_product="TransactionLedgerWindow",
                source_product_version="v1",
            ),
        ),
        coverage="complete",
    )

    assert vector.coverage == "complete"
