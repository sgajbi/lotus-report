"""Idempotency identity is the CLIENT's request - stable across server
enrichment deployments (the #312 merged-review finding, closed).

Server-derived recovery metadata injected into request options must never
change the identity of an unchanged accepted request: a pre-metadata
record retried after the enriching deployment, and a post-metadata record
retried after the hash stopped including enrichment, both return the
ORIGINAL job - while changed business intent still conflicts, and neither
path creates duplicate work.
"""

from __future__ import annotations

import pytest

from app.idea_evidence_intake.materialization_contract import (
    IDEA_MATERIALIZATION_RECOVERY_IDENTITY_OPTION,
)
from app.reporting_jobs.ledger import (
    SERVER_DERIVED_REQUEST_OPTION_KEYS,
    IdempotencyConflictError,
    ReportJobLedger,
    compute_request_hash,
)
from app.reporting_jobs.models import PortfolioReviewJobRequest, ReportCallerContext


def _caller() -> ReportCallerContext:
    return ReportCallerContext(
        trigger_type="user",
        triggered_by="advisor-123",
        caller_application="lotus-gateway",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role=None,
        correlation_id="corr-identity",
        trace_id="trace-identity",
    )


def _request(options: dict | None = None, portfolio: str = "PB_SG_GLOBAL_BAL_001"):
    return PortfolioReviewJobRequest.model_validate(
        {
            "portfolio_scope": {"portfolio_ids": [portfolio]},
            "as_of_date": "2026-04-22",
            "requested_output_formats": ["pdf"],
            "reporting_currency": "USD",
            "options": {"sections": ["OVERVIEW"], **(options or {})},
        }
    )


_INJECTED = {
    IDEA_MATERIALIZATION_RECOVERY_IDENTITY_OPTION: {
        "identity_version": "v1",
        "candidate_id": "cand-1",
    }
}


def test_the_registry_pins_the_owning_modules_constant() -> None:
    """One reserved key, two modules: the ledger's exclusion registry and
    the intake module's constant must never drift apart."""

    assert IDEA_MATERIALIZATION_RECOVERY_IDENTITY_OPTION in SERVER_DERIVED_REQUEST_OPTION_KEYS


def test_server_enrichment_never_changes_the_request_identity() -> None:
    plain = compute_request_hash(
        report_type="portfolio_review", request=_request(), caller_context=_caller()
    )
    enriched = compute_request_hash(
        report_type="portfolio_review",
        request=_request(options=_INJECTED),
        caller_context=_caller(),
    )

    assert plain == enriched


def test_a_pre_metadata_record_accepts_the_identical_enriched_retry(tmp_path) -> None:
    """Reproduction: a materialization created BEFORE the enriching
    deployment, retried identically after it - the retry now carries the
    injected option and must converge on the original job."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    original = ledger.submit_portfolio_review_job(
        request=_request(),
        caller_context=_caller(),
        idempotency_key="idem-pre-metadata",
    )

    retried = ledger.submit_portfolio_review_job(
        request=_request(options=_INJECTED),
        caller_context=_caller(),
        idempotency_key="idem-pre-metadata",
    )

    assert retried.job_id == original.job_id
    assert len(ledger.claim_work_items(worker_id="w", limit=10, lease_seconds=60)) == 1


def test_a_post_metadata_record_accepts_the_identical_retry_transitionally(
    tmp_path,
) -> None:
    """A record stored while the enrichment participated in the hash: the
    identical retry matches the transitional (enrichment-inclusive) form -
    the enrichment is deterministic from the request, so only a genuinely
    identical retry can reproduce it."""

    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    original = ledger.submit_portfolio_review_job(
        request=_request(options=_INJECTED),
        caller_context=_caller(),
        idempotency_key="idem-post-metadata",
    )
    legacy_hash = compute_request_hash(
        report_type="portfolio_review",
        request=_request(options=_INJECTED),
        caller_context=_caller(),
        include_server_derived_options=True,
    )
    with ledger._connect() as connection:  # simulate the pre-fix stored form
        connection.execute(
            "UPDATE report_request SET request_hash = ? WHERE idempotency_key = ?",
            (legacy_hash, "idem-post-metadata"),
        )

    retried = ledger.submit_portfolio_review_job(
        request=_request(options=_INJECTED),
        caller_context=_caller(),
        idempotency_key="idem-post-metadata",
    )

    assert retried.job_id == original.job_id
    assert len(ledger.claim_work_items(worker_id="w", limit=10, lease_seconds=60)) == 1


@pytest.mark.parametrize("stored_form", ["current", "legacy"])
def test_changed_business_intent_still_conflicts(tmp_path, stored_form) -> None:
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    ledger.submit_portfolio_review_job(
        request=_request(options=_INJECTED),
        caller_context=_caller(),
        idempotency_key="idem-conflict",
    )
    if stored_form == "legacy":
        legacy_hash = compute_request_hash(
            report_type="portfolio_review",
            request=_request(options=_INJECTED),
            caller_context=_caller(),
            include_server_derived_options=True,
        )
        with ledger._connect() as connection:
            connection.execute(
                "UPDATE report_request SET request_hash = ? WHERE idempotency_key = ?",
                (legacy_hash, "idem-conflict"),
            )

    with pytest.raises(IdempotencyConflictError):
        ledger.submit_portfolio_review_job(
            request=_request(options=_INJECTED, portfolio="PB_SG_OTHER_001"),
            caller_context=_caller(),
            idempotency_key="idem-conflict",
        )
