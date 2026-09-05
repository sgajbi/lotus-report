from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.reporting_jobs.ledger import ReportJobLedger
from app.reporting_jobs.models import (
    OutcomeReviewReportJobRequest,
    PortfolioReviewJobRequest,
    ProofPackReportJobRequest,
    ReportCallerContext,
    WaveReportJobRequest,
)
from app.reporting_lineage.models import ReportInputSnapshotCreateRequest
from app.reporting_lineage.store import ReportInputSnapshotStore
from app.reporting_render import service as render_service
from app.reporting_render.document_reference import derive_archive_request_id
from app.reporting_render.package_builder import (
    _advisor_memo_disclosure_refs,
    _advisor_memo_lineage_refs,
    _advisor_proposal_memo,
    _advisor_proposal_memo_archive_summary,
    _allocation_bucket_rows,
    _build_render_package,
    _date_text,
    _dedupe_strings,
    _holding_observation,
    _optional_decimal,
    _optional_int,
    _optional_str,
    _performance_history,
    _performance_observation,
    _positions,
    _reviewed_advisory_narrative,
    _reviewed_narrative_disclosure_refs,
    _risk_observation,
    _transactions,
)
from app.reporting_render.service import PortfolioReviewRenderOrchestrationService


class _RenderClientSuccess:
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        assert correlation_id == "corr-render"
        assert trace_id == "trace-render"
        assert payload["report_data"]["client_name"] == "Alex Tan"
        assert payload["report_data"]["performance_periods"][0]["period"] == "YTD"
        assert payload["report_data"]["performance_summary_table"][0]["label"] == "Year-to-date"
        assert payload["report_data"]["performance_monthly_history"][0]["period"] == "2026-01"
        return 201, {
            "render_job_id": payload["render_job_id"],
            "status": "rendered",
            "template_id": payload["template_id"],
            "template_version": payload["template_version"],
            "artifact_sha256": "sha256:artifact",
            "bounded_determinism_fingerprint": "fingerprint",
            "runtime_engine": "typst",
            "runtime_engine_version": "0.14.2",
            "render_duration_ms": 812,
            "artifact_base64": "JVBERi0xLjQKJQ==",
            "archive_state": "archived_verified",
            "archive_document_id": "doc_archived",
        }


class _RenderClientSuccessWithoutArtifact:
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        return 201, {
            "render_job_id": payload["render_job_id"],
            "status": "rendered",
            "template_id": payload["template_id"],
            "template_version": payload["template_version"],
            "artifact_sha256": "sha256:artifact",
            "bounded_determinism_fingerprint": "fingerprint",
            "runtime_engine": "typst",
            "runtime_engine_version": "0.14.2",
            "render_duration_ms": 812,
            "archive_state": "archived_verified",
            "archive_document_id": "doc_archived",
        }


class _RenderClientFailure:
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        return 422, {
            "detail": {
                "code": "render_package_invalid",
                "message": "Template payload was invalid.",
            }
        }


class _RenderClientConflict:
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        return 409, {
            "detail": {
                "code": "render_job_conflict",
                "message": "Render job already exists.",
            }
        }


class _RenderClientServerError:
    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        return 503, {"failure_message": "lotus-render unavailable"}


class _RenderClientWithArchiveOutcome:
    """Rendered successfully; the archive handoff (Render's leg) reported the
    given custody state - the exact response shapes lotus-render publishes."""

    def __init__(self, archive_state, archive_document_id=None, archive_detail=None):
        self._archive_state = archive_state
        self._archive_document_id = archive_document_id
        self._archive_detail = archive_detail

    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        return 201, {
            "render_job_id": payload["render_job_id"],
            "status": "rendered",
            "template_id": payload["template_id"],
            "template_version": payload["template_version"],
            "artifact_sha256": "sha256:artifact",
            "bounded_determinism_fingerprint": "fingerprint",
            "runtime_engine": "typst",
            "runtime_engine_version": "0.14.2",
            "render_duration_ms": 812,
            "artifact_base64": "JVBERi0xLjQKJQ==",
            "archive_state": self._archive_state,
            "archive_document_id": self._archive_document_id,
            "archive_detail": self._archive_detail,
        }


def _job_request(**overrides):
    payload = {
        "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
        "as_of_date": "2026-04-22",
        "requested_output_formats": ["pdf"],
        "reporting_currency": "USD",
        "options": {"sections": ["OVERVIEW", "PERFORMANCE"]},
    }
    payload.update(overrides)
    return PortfolioReviewJobRequest.model_validate(payload)


def _proposal_narrative_package() -> dict:
    return {
        "package_status": "INCLUDED_REVIEWED_NARRATIVE",
        "usage": "REPORT_REQUEST_APPROVED_ADVISOR_NARRATIVE",
        "proposal_id": "prop_001",
        "proposal_version_no": 3,
        "narrative_id": "pnar_001",
        "narrative_status": "APPROVED_FOR_ADVISOR_USE",
        "audience": "advisor",
        "policy_version": "proposal-narrative-policy.v1",
        "review": {
            "review_id": "pnrev_001",
            "review_state": "APPROVED_FOR_ADVISOR_USE",
            "reviewed_at": "2026-04-22T09:10:00Z",
            "reviewed_by": "advisor-123",
        },
        "source_lineage": {
            "source_narrative_hash": "sha256:narrative",
            "proposal_hash": "sha256:proposal",
            "proposal_version_hash": "sha256:proposal-version",
        },
        "sections": [
            {
                "section_id": "portfolio_context",
                "title": "Portfolio Context",
                "body": "The portfolio remains aligned to the balanced mandate.",
                "source_refs": [{"source_system": "lotus-advise", "source_id": "prop_001"}],
            }
        ],
        "disclosures": [
            {
                "disclosure_id": "proposal_narrative.advisor_use_only.v1",
                "text": "For advisor use only until the client-ready workflow is approved.",
            }
        ],
        "guardrail_results": [{"guardrail_id": "no_trade_instruction", "status": "passed"}],
        "limitations": [{"limitation_id": "advisor_use_only", "status": "active"}],
        "execution_boundary": {"client_distribution_allowed": False},
    }


def _caller():
    return ReportCallerContext.model_validate(
        {
            "triggered_by": "advisor-123",
            "caller_application": "lotus-gateway",
            "tenant_id": "tenant-sg",
            "region": "APAC",
            "booking_center_code": "SG",
            "role": "advisor",
            "correlation_id": "corr-render",
            "trace_id": "trace-render",
        }
    )


def _proof_pack_report_input() -> dict:
    return {
        "contract_version": "1.0",
        "proof_pack_id": "dpp_001",
        "proof_pack_content_hash": "sha256:proof-pack",
        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        "mandate_id": "MANDATE_PB_SG_GLOBAL_BAL_001",
        "as_of_date": "2026-05-03",
        "generated_at": "2026-05-03T09:00:00Z",
        "report_title": "Pre-Trade Proof Pack - PB_SG_GLOBAL_BAL_001",
        "report_audience": ["portfolio_manager", "investment_control", "audit"],
        "state": "READY",
        "decision_summary": {
            "recommended_action": "approve_rebalance",
            "rationale": "Mandate drift and source readiness support rebalance approval.",
        },
        "supportability": {"status": "READY", "reason_codes": ["proof_pack_ready"]},
        "sections": [
            {
                "section_id": "sec_mandate",
                "section_type": "MANDATE_CONTEXT",
                "state": "READY",
                "title": "Mandate context",
                "summary": "Mandate, model, and policy evidence are aligned.",
                "reason_codes": ["mandate_context_ready"],
                "facts": {},
                "metrics": {},
                "evidence_refs": [],
                "source_refs": [],
                "content_hash": "sha256:section-mandate",
            }
        ],
        "markdown_summary": "# Pre-Trade Proof Pack",
        "source_hashes": {"mandate": "sha256:mandate"},
        "redaction_policy": "NO_RAW_PAYLOADS",
        "retention_policy": "generated-report-standard",
        "evidence_ref": {
            "source_system": "lotus-manage",
            "source_type": "DPM_PROOF_PACK_REPORT_INPUT",
            "source_id": "dpp_001:dpm_proof_pack_report_input",
            "content_hash": "sha256:report-input",
        },
        "content_hash": "sha256:report-input",
    }


def _portfolio_memory_context() -> dict:
    return {
        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        "supportability_state": "READY",
        "event_count": 3,
        "source_systems": ["lotus-manage"],
        "reason_codes": ["proof_pack_ready"],
        "content_hash": "sha256:portfolio-memory",
        "context_content_hash": "sha256:portfolio-memory-context",
        "support_boundary": "BOUNDED_EVENT_REFS_ONLY",
        "event_ref_limit": 12,
        "event_ref_selection_policy": "MOST_RECENT_RELEVANT_FIRST",
        "event_refs_returned": 1,
        "event_refs_omitted": 2,
        "event_refs_truncated": True,
        "governance_policy": {
            "retention_policy": "DPM_PORTFOLIO_MEMORY_SOURCE_LINEAGE_7Y",
            "redaction_policy": "NO_RAW_PAYLOADS",
            "audit_policy": "AUDIT_READ_AND_EXPORT",
            "access_classification": "CLIENT_CONFIDENTIAL_INTERNAL",
        },
        "event_refs": [
            {
                "event_identity": "lotus-manage:DPM_PROOF_PACK:dpp_001:sha256:proof-pack",
                "event_type": "PROOF_PACK_CREATED",
                "source_system": "lotus-manage",
                "source_type": "DPM_PROOF_PACK",
                "source_id": "dpp_001",
                "content_hash": "sha256:proof-pack",
                "retention_policy": "DPM_PORTFOLIO_MEMORY_SOURCE_LINEAGE_7Y",
                "redaction_policy": "NO_RAW_PAYLOADS",
                "audit_policy": "AUDIT_READ_AND_EXPORT",
                "access_classification": "CLIENT_CONFIDENTIAL_INTERNAL",
                "event_time": "2026-05-03T08:59:00Z",
                "event_ref_selection_rank": 1,
                "manage_lookup_id": "pmem_lookup_dpp_001",
            }
        ],
    }


def _assert_portfolio_memory_controls(memory: dict) -> None:
    assert memory["context_content_hash"] == "sha256:portfolio-memory-context"
    assert memory["support_boundary"] == "BOUNDED_EVENT_REFS_ONLY"
    assert memory["event_ref_limit"] == 12
    assert memory["event_ref_selection_policy"] == "MOST_RECENT_RELEVANT_FIRST"
    assert memory["event_refs_returned"] == 1
    assert memory["event_refs_omitted"] == 2
    assert memory["event_refs_truncated"] is True
    assert memory["event_refs"][0]["event_time"] == "2026-05-03T08:59:00Z"
    assert memory["event_refs"][0]["event_ref_selection_rank"] == 1
    assert memory["event_refs"][0]["manage_lookup_id"] == "pmem_lookup_dpp_001"


def _seed_data_ready_job(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    store = ReportInputSnapshotStore(tmp_path / "lineage.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-render",
    )
    ready = ledger.mark_data_ready(
        job_id=job.job_id,
        actor=job.triggered_by,
        correlation_id=job.correlation_id,
        trace_id=job.trace_id,
    )
    store.create_snapshot(
        ReportInputSnapshotCreateRequest(
            report_job_id=job.job_id,
            report_type=job.report_type,
            report_data_contract_version="v1",
            portfolio_scope=job.portfolio_scope,
            as_of_date=job.as_of_date,
            snapshot_payload={
                "readiness": {"status": "ready"},
                "reportingCurrency": "USD",
                "reviewPeriod": {"label": "YTD"},
                "clientProfile": {
                    "identity": {
                        "client_name": "Alex Tan",
                        "advisor_id": "RM_SG_001",
                        "booking_center_code": "Singapore",
                    },
                    "mandate_profile": {"risk_exposure": "balanced"},
                },
                "overview": {"total_market_value": 15234567.89, "currency": "USD"},
                "performance": {
                    "summary": {
                        "YTD": {
                            "net_cumulative_return": 4.1,
                            "benchmark_cumulative_return": 3.4,
                            "benchmark_relative_return": 0.7,
                        },
                        "1Y": {
                            "net_cumulative_return": 7.08,
                            "net_annualized_return": 7.08,
                        },
                    },
                    "monthly_history": [
                        {
                            "period": "2026-01",
                            "period_start": "2026-01-01",
                            "period_end": "2026-01-31",
                            "end_market_value": 5_214_639.0,
                            "inflows": 5_841_778.0,
                            "outflows": -5_841_749.0,
                            "performance_value": -87_158.0,
                            "cumulative_performance_value": -87_158.0,
                            "twr_pct": -1.64,
                            "cumulative_twr_pct": -1.64,
                        }
                    ],
                    "annual_history": [
                        {
                            "period": "2025",
                            "period_start": "2025-01-01",
                            "period_end": "2025-12-31",
                            "end_market_value": 5_296_856.0,
                            "inflows": 80_000.0,
                            "outflows": -20_000.0,
                            "performance_value": 226_856.0,
                            "cumulative_performance_value": 226_856.0,
                            "twr_pct": 4.5,
                            "cumulative_twr_pct": 12.0,
                        }
                    ],
                },
                "riskAnalytics": {
                    "summary": {
                        "YTD": {
                            "volatility": 12.0,
                            "beta": 0.82,
                            "tracking_error": 4.0,
                            "information_ratio": 0.72,
                            "value_at_risk": -2.0,
                        }
                    }
                },
                "holdings": {
                    "holdingsByAssetClass": {
                        "Equity": [
                            {
                                "security_id": "EQ-1",
                                "instrument_name": "Global Equity Sleeve",
                                "isin": "US0000000001",
                                "quantity": 2200.0,
                                "position_date": "2026-04-22",
                                "product_type": "Fund",
                                "sector": "Technology",
                                "country_of_risk": "United States",
                                "rating": "A",
                                "liquidity_tier": "High",
                                "held_since_date": "2024-01-15",
                                "market_price": 102.35,
                                "cost_basis_reporting_currency": 500000.0,
                                "weight": 60.0,
                                "market_value_reporting_currency": 600000.0,
                                "unrealized_pnl_reporting_currency": 100000.0,
                                "unrealized_pnl_pct": 20.0,
                                "ytd_contribution_pct": 3.5,
                                "ytd_average_weight_pct": 55.0,
                                "ytd_total_return_pct": 8.4,
                                "currency": "USD",
                            }
                        ]
                    }
                },
                "transactions": {
                    "transactionsByCategory": {
                        "Trading": [
                            {
                                "transaction_id": "TXN-1",
                                "transaction_date": "2026-04-18",
                                "transaction_type": "SELL",
                                "instrument_id": "INST-1",
                                "security_id": "EQ-1",
                                "transaction_category": "Trading",
                                "display_label": "Sell",
                                "cash_leg": False,
                                "asset_class": "Equity",
                                "amount_reporting_currency": 25000.0,
                                "gross_transaction_amount_reporting_currency": 25000.0,
                                "realized_pnl_reporting_currency": 1250.0,
                                "realized_pnl_local": 1250.0,
                                "net_interest_amount_reporting_currency": 0.0,
                                "withholding_tax_amount_reporting_currency": 0.0,
                                "income_or_tax_reporting_currency": 0.0,
                            }
                        ]
                    },
                    "transactionCount": 1,
                },
                "reviewObservations": [
                    {
                        "observation_id": "obs-1",
                        "summary": (
                            "Equity sleeve remained the main performance driver "
                            "through the review period."
                        ),
                    }
                ],
                "evidence": {
                    "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
                    "trust_metadata": {
                        "completeness_status": "complete",
                        "data_quality_status": "quality_passed",
                    },
                },
                "keyFigures": {
                    "client_profile": {
                        "objective": (
                            "Long-term real wealth growth with controlled income and liquidity."
                        )
                    },
                    "portfolio_value": {
                        "invested_market_value_reporting_currency": 14984567.89,
                        "cash_balance_reporting_currency": 250000.0,
                        "cash_weight_pct": 1.64,
                    },
                    "allocation": {
                        "name": "Equity",
                        "weight_pct": 60.0,
                        "market_value_reporting_currency": 600000.0,
                        "position_count": 3,
                    },
                    "performance": {
                        "largest_positive_contributor": {
                            "security_name": "Global Equity Sleeve",
                            "total_contribution_pct": 3.5,
                        }
                    },
                    "risk": {"ytd_volatility_pct": 12.0, "ytd_beta": 0.82},
                    "holdings": {"position_count": 12},
                },
            },
            snapshot_storage_ref=None,
            supportability_status="complete",
            completeness_status="complete",
            lineage_summary={"source_services": ["lotus-core"], "call_count": 1},
            captured_at=datetime.now(UTC),
            correlation_id=job.correlation_id,
            trace_id=job.trace_id,
        )
    )
    return ledger, store, ready


def _advisor_commentary_package(**overrides) -> dict:
    package = {
        "status": "included",
        "schema_id": "lotus-ai.workflow_pack_run.accepted_output.advisor_brief.v1",
        "run_id": "run_accept_1",
        "pack_id": "advisor_brief.pack",
        "pack_version": "v1",
        "task_id": "task_1",
        "request_id": "req_77",
        "workflow_authority_owner": "lotus-performance",
        "review": {"reviewed_by": "advisor-lead-7", "reviewed_at": "2026-04-21T10:00:00Z"},
        "advisor_brief_status": "complete",
        "coverage_state": "full",
        "grounded_summary": "Reviewed summary of portfolio performance.",
        "talking_points": [
            {
                "headline": "Equity allocation drove returns",
                "detail": "Overweight global equities contributed 1.2%.",
                "tone": "positive",
                "evidence_refs": [
                    {
                        "metric_label": "Equity Contribution",
                        "metric_value": "1.2%",
                        "source_ref": "performance:contribution:equities",
                    }
                ],
            }
        ],
        "risks_and_exceptions": [],
        "context": {
            "portfolio_id": "PB_SG_GLOBAL_BAL_001",
            "period": "YTD",
            "as_of_date": "2026-04-22",
            "reporting_currency": "USD",
            "benchmark": None,
        },
        "source_refs": ["performance:workspace-summary"],
        "evidence_types": ["metric_evidence"],
        "content_hash": "0c" * 32,
        "content_hash_algorithm": "sha256",
        "notes": [],
        "disclosure_text": (
            "Commentary generated with AI assistance and reviewed by advisor-lead-7 "
            "on 2026-04-21T10:00:00Z; run run_accept_1."
        ),
    }
    package.update(overrides)
    return package


def test_render_package_carries_the_resolved_allocation_decision(tmp_path):
    """Render must be told which dimensions to present; it previously had to
    guess from which by_* keys had rows, and guessed wrong for six of the
    seven single-dimension orders (issue #224)."""

    ledger, _store, ready = _seed_data_ready_job(tmp_path)
    snapshot_payload = {
        "readiness": {"status": "ready"},
        "reportingCurrency": "USD",
        "clientProfile": {"identity": {"client_name": "Alex Tan"}},
        "overview": {"total_market_value": 1000.0, "currency": "USD"},
        "allocation": {
            "bySector": [{"name": "Financials", "weight_pct": 55.0}],
            "byCurrency": [{"name": "USD", "weight_pct": 100.0}],
            "byRegion": [],
        },
        "allocation_presentation": {
            "resolved_by": "caller_request",
            "dimensions": [
                {"dimension": "sector", "package_key": "by_sector", "posture": "ready"},
                {"dimension": "region", "package_key": "by_region", "posture": "empty"},
                {"dimension": "rating", "package_key": "by_rating", "posture": "unavailable"},
            ],
        },
    }

    package = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot=snapshot_payload,
        render_job_id="rdr_test_pdf",
        snapshot_id="rsnap_test_durable",
    )

    presentation = package["report_data"]["allocation_presentation"]
    assert presentation["resolved_by"] == "caller_request"
    assert [entry["dimension"] for entry in presentation["dimensions"]] == [
        "sector",
        "region",
        "rating",
    ]
    assert [entry["posture"] for entry in presentation["dimensions"]] == [
        "ready",
        "empty",
        "unavailable",
    ]
    # All seven breakdowns still ship as evidence: an operator diagnosing
    # "why no sector" must be able to look at the rows. Presence carries no
    # presentation meaning - the resolved list does.
    assert "by_currency" in package["report_data"]["allocation_breakdowns"]
    assert "currency" not in {entry["dimension"] for entry in presentation["dimensions"]}


def test_every_package_carries_the_document_reference_and_attempts_share_it(tmp_path):
    """The governed identity a client document carries in its footer, minted
    in the ONE envelope all four families share. Two render ATTEMPTS at the
    same job and snapshot are the same governed document: different
    render_job_id, same reference - per-attempt values are not even inputs.
    A different snapshot is a different document."""

    ledger, _store, ready = _seed_data_ready_job(tmp_path)
    # Production-shaped: the payload does NOT carry snapshot_id - the durable
    # identity lives on the ReportInputSnapshotRecord and crosses the boundary
    # as its own parameter, never rediscovered from the payload.
    snapshot_payload = {
        "readiness": {"status": "ready"},
        "reportingCurrency": "USD",
        "clientProfile": {"identity": {"client_name": "Alex Tan"}},
        "overview": {"total_market_value": 1000.0, "currency": "USD"},
    }

    first_attempt = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot=snapshot_payload,
        render_job_id="rdr_attempt_1",
        snapshot_id="rsnap_doc_1",
    )
    second_attempt = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot=snapshot_payload,
        render_job_id="rdr_attempt_2",
        snapshot_id="rsnap_doc_1",
    )
    regenerated = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot=snapshot_payload,
        render_job_id="rdr_attempt_3",
        snapshot_id="rsnap_doc_2",
    )

    reference = first_attempt["render_context"]["document_reference"]
    assert reference.startswith("rdoc_")
    assert second_attempt["render_context"]["document_reference"] == reference
    assert regenerated["render_context"]["document_reference"] != reference


def test_a_report_that_did_not_order_risk_gets_no_risk_panel(tmp_path):
    """`riskAnalytics` is composed only when RISK_ANALYTICS is requested, so
    its absence means the caller deselected the section - not that a
    measurement was attempted and failed.

    The panel used to be drawn regardless, as five "Not available" cells, on
    reports that never asked for one. In a governed client document that reads
    as a statement about the portfolio's risk, and it was never true. Render
    draws nothing for an empty mapping, so the panel simply does not appear.
    """

    ledger, _store, ready = _seed_data_ready_job(tmp_path)
    snapshot_payload = {
        "readiness": {"status": "ready"},
        "reportingCurrency": "USD",
        "clientProfile": {"identity": {"client_name": "Alex Tan"}},
        "overview": {"total_market_value": 1000.0, "currency": "USD"},
    }

    package = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot=snapshot_payload,
        render_job_id="rdr_test_pdf",
        snapshot_id="rsnap_test_durable",
    )

    assert package["report_data"]["risk_summary"] == {}


def test_an_ordered_risk_section_still_draws_when_the_data_is_unavailable(tmp_path):
    """The counterpart, and the reason absence alone cannot be the signal: a
    section the order PROMISED is never silently omitted. When risk was
    ordered and lotus-risk could not answer, the panel is still drawn and says
    so, rather than vanishing as though it had not been requested."""

    ledger, _store, ready = _seed_data_ready_job(tmp_path)
    snapshot_payload = {
        "readiness": {"status": "ready"},
        "reportingCurrency": "USD",
        "clientProfile": {"identity": {"client_name": "Alex Tan"}},
        "overview": {"total_market_value": 1000.0, "currency": "USD"},
        "riskAnalytics": {
            "supportability": {
                "status": "unavailable",
                "notes": [{"code": "risk_upstream_failure", "severity": "blocking"}],
            },
        },
    }

    package = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot=snapshot_payload,
        render_job_id="rdr_test_pdf",
        snapshot_id="rsnap_test_durable",
    )

    risk_summary = package["report_data"]["risk_summary"]
    assert risk_summary != {}
    assert risk_summary["volatility_pct"] == "Not available"


def test_a_snapshot_without_the_decision_resolves_it_rather_than_guessing(tmp_path):
    """A rerender of a job captured before this key existed presents what that
    order asked for, not what a renderer would have inferred."""

    ledger, _store, ready = _seed_data_ready_job(tmp_path)
    job = ledger.get_job(ready.job_id)
    snapshot_payload = {
        "readiness": {"status": "ready"},
        "reportingCurrency": "USD",
        "clientProfile": {"identity": {"client_name": "Alex Tan"}},
        "overview": {"total_market_value": 1000.0, "currency": "USD"},
        "allocation": {"byAssetClass": [{"name": "Equity", "weight_pct": 60.0}]},
    }

    package = _build_render_package(
        job=job,
        snapshot=snapshot_payload,
        render_job_id="rdr_test_pdf",
        snapshot_id="rsnap_test_durable",
    )

    presentation = package["report_data"]["allocation_presentation"]
    assert presentation["dimensions"], "an older snapshot must still name its dimensions"
    assert presentation["resolved_by"] in {"caller_request", "report_default_policy"}


def test_portfolio_review_render_package_includes_advisor_commentary(tmp_path):
    ledger, _store, ready = _seed_data_ready_job(tmp_path)
    snapshot_payload = {
        "readiness": {"status": "ready"},
        "reportingCurrency": "USD",
        "clientProfile": {
            "identity": {"client_name": "Alex Tan"},
            "mandate_profile": {"risk_exposure": "balanced"},
        },
        "overview": {"total_market_value": 15234567.89, "currency": "USD"},
        "advisor_commentary_package": _advisor_commentary_package(),
    }

    package = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot=snapshot_payload,
        render_job_id="rdr_test_pdf",
        snapshot_id="rsnap_test_durable",
    )

    commentary = package["report_data"]["advisor_commentary"]
    assert commentary["status"] == "included"
    assert commentary["run_id"] == "run_accept_1"
    assert commentary["review"]["reviewed_by"] == "advisor-lead-7"
    assert commentary["talking_points"][0]["headline"] == "Equity allocation drove returns"
    assert "reviewed by advisor-lead-7" in commentary["disclosure_text"]
    assert "run_accept_1" in package["lineage_refs"]
    assert "0c" * 32 in package["lineage_refs"]


def test_portfolio_review_render_package_reports_closed_advisor_commentary(tmp_path):
    ledger, _store, ready = _seed_data_ready_job(tmp_path)
    snapshot_payload = {
        "readiness": {"status": "ready"},
        "reportingCurrency": "USD",
        "clientProfile": {
            "identity": {"client_name": "Alex Tan"},
            "mandate_profile": {"risk_exposure": "balanced"},
        },
        "overview": {"total_market_value": 15234567.89, "currency": "USD"},
        "advisor_commentary_package": {
            "status": "unavailable",
            "reason_code": "advisor_brief_not_reviewed",
            "advisor_brief_run_id": "run_pending",
        },
    }

    package = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot=snapshot_payload,
        render_job_id="rdr_test_pdf",
        snapshot_id="rsnap_test_durable",
    )

    commentary = package["report_data"]["advisor_commentary"]
    assert commentary["status"] == "unavailable"
    assert commentary["reason_code"] == "advisor_brief_not_reviewed"
    assert "run_pending" not in package["lineage_refs"]

    absent = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot={
            "readiness": {"status": "ready"},
            "reportingCurrency": "USD",
            "clientProfile": {
                "identity": {"client_name": "Alex Tan"},
                "mandate_profile": {"risk_exposure": "balanced"},
            },
            "overview": {"total_market_value": 15234567.89, "currency": "USD"},
        },
        render_job_id="rdr_test_pdf",
        snapshot_id="rsnap_test_durable",
    )
    assert absent["report_data"]["advisor_commentary"] == {"status": "not_supplied"}


def test_advisor_commentary_archive_summary_keeps_audit_identity():
    from app.reporting_render.package_builder import _advisor_commentary_archive_summary

    summary = _advisor_commentary_archive_summary(
        {"advisor_commentary_package": _advisor_commentary_package()}
    )
    assert summary == {
        "run_id": "run_accept_1",
        "request_id": "req_77",
        "reviewed_by": "advisor-lead-7",
        "reviewed_at": "2026-04-21T10:00:00Z",
        "content_hash": "0c" * 32,
        "schema_id": "lotus-ai.workflow_pack_run.accepted_output.advisor_brief.v1",
        "included_in_render": True,
    }
    assert (
        _advisor_commentary_archive_summary(
            {
                "advisor_commentary_package": {
                    "status": "unavailable",
                    "reason_code": "advisor_brief_not_found",
                }
            }
        )
        is None
    )
    assert _advisor_commentary_archive_summary({}) is None


def test_portfolio_review_render_package_includes_reviewed_advisory_narrative(tmp_path):
    ledger, _store, ready = _seed_data_ready_job(tmp_path)
    snapshot_payload = {
        "readiness": {"status": "ready"},
        "reportingCurrency": "USD",
        "clientProfile": {
            "identity": {
                "client_name": "Alex Tan",
                "advisor_id": "RM_SG_001",
                "booking_center_code": "Singapore",
            },
            "mandate_profile": {"risk_exposure": "balanced"},
        },
        "overview": {"total_market_value": 15234567.89, "currency": "USD"},
        "keyFigures": {
            "client_profile": {"objective": "Long-term real wealth growth."},
            "portfolio_value": {
                "invested_market_value_reporting_currency": 14984567.89,
                "cash_balance_reporting_currency": 250000.0,
                "cash_weight_pct": 1.64,
            },
            "allocation": {"name": "Equity", "weight_pct": 60.0},
        },
        "evidence": {
            "source_services": [
                "lotus-core",
                "lotus-performance",
                "lotus-risk",
                "lotus-advise",
            ],
            "trust_metadata": {
                "completeness_status": "complete",
                "data_quality_status": "quality_passed",
            },
        },
        "proposal_narrative_package": _proposal_narrative_package(),
    }

    package = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot=snapshot_payload,
        render_job_id="rdr_test_pdf",
        snapshot_id="rsnap_test_durable",
    )

    narrative = package["report_data"]["reviewed_advisory_narrative"]
    assert narrative["status"] == "included"
    assert narrative["proposal_id"] == "prop_001"
    assert narrative["review"]["review_state"] == "APPROVED_FOR_ADVISOR_USE"
    assert narrative["source_lineage"]["source_narrative_hash"] == "sha256:narrative"
    assert narrative["sections"][0]["body"] == (
        "The portfolio remains aligned to the balanced mandate."
    )
    assert "lotus-advise:proposal:prop_001" in package["lineage_refs"]
    assert "sha256:narrative" in package["lineage_refs"]
    assert "proposal_narrative.advisor_use_only.v1" in package["disclosure_refs"]


def test_portfolio_review_render_package_includes_advisor_proposal_memo(tmp_path):
    ledger, _store, ready = _seed_data_ready_job(tmp_path)
    snapshot_payload = {
        "readiness": {"status": "ready"},
        "reportingCurrency": "USD",
        "clientProfile": {"identity": {"client_name": "Alex Tan"}},
        "overview": {"total_market_value": 15234567.89, "currency": "USD"},
        "keyFigures": {},
        "proposal_memo_package": {
            "package_status": "INCLUDED_ADVISOR_PROPOSAL_MEMO",
            "usage": "REPORT_REQUEST_APPROVED_ADVISOR_MEMO",
            "memo_id": "memo_001",
            "memo_version": "advisory-proposal-memo-evidence-pack.v1",
            "memo_status": "READY",
            "proposal_id": "prop_001",
            "proposal_version_no": 1,
            "memo_hash": "sha256:memo",
            "source_input_hash": "sha256:source",
            "review": {
                "review_event_id": "pme_review_001",
                "review_action": "APPROVE_FOR_ADVISOR_USE",
                "reviewed_by": "compliance_1",
            },
            "sections": [
                {
                    "section_id": "EXECUTIVE_SUMMARY",
                    "title": "Executive Summary",
                    "status": "READY",
                    "summary": "Advisor memo is ready for advisor use.",
                    "material_claims": [
                        {"claim_id": "memo.summary", "text": "Advisor-use memo claim."}
                    ],
                },
                {
                    "section_id": "CONFLICTS_AND_DISCLOSURES",
                    "title": "Conflicts and Disclosures",
                    "status": "READY",
                    "summary": "Disclosures are attached.",
                    "material_claims": [
                        {
                            "claim_id": "memo.disclosure.advisor_use_only",
                            "text": "Advisor use only.",
                        }
                    ],
                },
            ],
            "client_ready_publication": "BLOCKED",
        },
    }

    package = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot=snapshot_payload,
        render_job_id="rdr_memo_pdf",
        snapshot_id="rsnap_test_durable",
    )

    memo = package["report_data"]["advisor_proposal_memo"]
    assert memo["status"] == "included"
    assert memo["memo_id"] == "memo_001"
    assert memo["review"]["review_action"] == "APPROVE_FOR_ADVISOR_USE"
    assert memo["client_ready_publication"] == "BLOCKED"
    assert memo["sections"][0]["summary"] == "Advisor memo is ready for advisor use."
    assert "lotus-advise:proposal-memo:memo_001" in package["lineage_refs"]
    assert "sha256:memo" in package["lineage_refs"]
    assert "memo.disclosure.advisor_use_only" in package["disclosure_refs"]


def test_reviewed_advisory_narrative_handles_optional_package_edges(tmp_path):
    package = _proposal_narrative_package()
    package["review"].pop("review_id")
    package["disclosures"] = "not-a-list"
    package["source_lineage"]["source_narrative_hash"] = "sha256:not_available"

    narrative = _reviewed_advisory_narrative({"proposal_narrative_package": package})

    assert narrative["review"]["review_id"] == "not_available"
    assert narrative["disclosures"] == []
    ledger = ReportJobLedger(tmp_path / "edge-jobs.sqlite3")
    rendered = _build_render_package(
        job=ledger.create_portfolio_review_job(
            request=_job_request(),
            caller_context=_caller(),
            idempotency_key="idem-reviewed-narrative-edge",
        ),
        snapshot={"proposal_narrative_package": package},
        render_job_id="rdr_edge_pdf",
        snapshot_id="rsnap_test_durable",
    )
    assert "lotus-advise:proposal-narrative-review:not_available" not in rendered["lineage_refs"]
    assert "sha256:not_available" not in rendered["lineage_refs"]


def test_advisor_proposal_memo_handles_absent_package() -> None:
    assert _advisor_proposal_memo({}) == {
        "status": "not_supplied",
        "sections": [],
        "disclosures": [],
    }


def test_advisor_proposal_memo_handles_optional_package_edges() -> None:
    memo = _advisor_proposal_memo(
        {
            "proposal_memo_package": {
                "package_status": "INCLUDED_ADVISOR_PROPOSAL_MEMO",
                "usage": "REPORT_REQUEST_APPROVED_ADVISOR_MEMO",
                "memo_id": "not_available",
                "proposal_id": "prop_001",
                "memo_hash": "sha256:not_available",
                "source_input_hash": "sha256:source",
                "review": {},
                "sections": [
                    "bad-section",
                    {
                        "section_id": "CONFLICTS_AND_DISCLOSURES",
                        "material_claims": [{"text": "Disclosure without id."}],
                    },
                ],
            }
        }
    )

    assert memo["review"]["review_event_id"] == "not_available"
    assert memo["disclosures"] == []
    assert _advisor_memo_lineage_refs(memo) == [
        "lotus-advise:proposal:prop_001",
        "sha256:source",
    ]
    assert _advisor_memo_disclosure_refs({"disclosures": "bad-disclosures"}) == []
    assert _reviewed_narrative_disclosure_refs({"disclosures": "bad-disclosures"}) == []
    assert _dedupe_strings(["one", "one", None, "two"]) == ["one", "two"]


def test_custody_block_carries_only_the_facts_report_owns(tmp_path):
    """The render_context.archive block is Report's half of the render#120
    handoff: request lineage, scope, period, residency, retention, and the
    advisor-memo audit summary. Everything Render overlays (identity,
    provenance, declared digest) and everything that lives in Report's own
    ledger (supersession lineage) is deliberately absent - a value stated
    here for an overlaid field would be silently discarded, which is worse
    than not stating it."""

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    snapshot = store.get_snapshot_by_job(ready.job_id)
    snapshot.snapshot_payload["proposal_memo_package"] = {
        "memo_id": "memo_001",
        "proposal_id": "prop_001",
        "proposal_version_no": 1,
        "memo_hash": "sha256:memo",
        "source_input_hash": "sha256:source",
        "review": {
            "review_event_id": "pme_review_001",
            "review_action": "APPROVE_FOR_ADVISOR_USE",
        },
        "sections": [{"section_id": "EXECUTIVE_SUMMARY"}],
        "client_ready_publication": "BLOCKED",
    }

    package = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot=snapshot.snapshot_payload,
        render_job_id="rdr_001",
        snapshot_id=snapshot.snapshot_id,
    )

    custody = package["render_context"]["archive"]
    assert custody["report_request_id"].startswith("rrq_")
    assert custody["portfolio_id"] == "PB_SG_GLOBAL_BAL_001"
    assert custody["portfolio_scope"] == '{"portfolio_ids":["PB_SG_GLOBAL_BAL_001"]}'
    assert custody["classification"] == "confidential"
    assert custody["tenant_id"] == "tenant-sg"
    assert custody["region"] == "APAC"
    assert custody["advisor_proposal_memo"]["memo_id"] == "memo_001"
    assert custody["advisor_proposal_memo"]["section_count"] == 1
    for overlaid in (
        "archive_request_id",
        "document_reference",
        "declared_artifact_sha256",
        "report_job_id",
        "snapshot_id",
        "render_job_id",
        "render_attempt_id",
        "created_by_service",
        "created_by_actor",
        "supersedes_render_job_id",
        "supersedes_archive_document_id",
        "archive_consequence",
    ):
        assert overlaid not in custody
    assert _advisor_proposal_memo_archive_summary({}) is None


class _RenderClientRecording:
    """Succeeds like the success fake but keeps the submitted package, so the
    regression can inspect what production actually sent."""

    def __init__(self):
        self.packages = []

    async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
        self.packages.append(payload)
        return 201, {
            "render_job_id": payload["render_job_id"],
            "status": "rendered",
            "template_id": payload["template_id"],
            "template_version": payload["template_version"],
            "artifact_sha256": "sha256:artifact",
            "bounded_determinism_fingerprint": "fingerprint",
            "runtime_engine": "typst",
            "runtime_engine_version": "0.13",
            "render_duration_ms": 812,
            "artifact_base64": "JVBERi0xLjQKJQ==",
            "archive_state": "archived_verified",
            "archive_document_id": "doc_archived",
        }


@pytest.mark.asyncio
async def test_document_identity_binds_the_durable_snapshot_end_to_end(tmp_path):
    """The invariant: the snapshot identity printed into a governed document
    is exactly the durable Report snapshot identity later recorded in Archive
    lineage.

    Production-shaped throughout: the durable ReportInputSnapshotRecord has a
    snapshot_id, the snapshot_payload does NOT - and nothing here injects one
    into the payload. Previously the envelope rediscovered the id from the
    payload and fell back to a synthetic snapshot-for-<job>, so the reference
    in the footer named evidence Archive lineage would never record, and the
    rerender path even overwrote the envelope's id AFTER the reference was
    minted - one package, two disagreeing identities.
    """

    from app.reporting_render.document_reference import mint_document_reference

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    record = store.get_snapshot_by_job(ready.job_id)
    assert record.snapshot_id.startswith("rsnap_")
    assert "snapshot_id" not in record.snapshot_payload

    render_client = _RenderClientRecording()
    service = PortfolioReviewRenderOrchestrationService(
        render_client=render_client,
        snapshot_store=store,
        job_ledger=ledger,
    )

    completed = await service.render_for_job(ready)
    assert completed.status == "archived"
    # The reconciliation identity Report records is derived from the governed
    # reference and the exact bytes - the same id Render presented to Archive.
    assert completed.archive_request_id == derive_archive_request_id(
        render_client.packages[0]["render_context"]["document_reference"],
        "sha256:artifact",
    )

    package = render_client.packages[0]
    expected_reference = mint_document_reference(
        report_job_id=ready.job_id,
        snapshot_id=record.snapshot_id,
        # The reference binds the pair persisted on the job at acceptance -
        # the current family default, whatever it is - not a fixed version.
        template_id=ready.render_template_id,
        template_version=ready.render_template_version,
    )
    # The package carries the durable record id and mints the reference
    # from it - never from the payload, never from a synthetic name.
    assert package["snapshot_id"] == record.snapshot_id
    assert package["render_context"]["document_reference"] == expected_reference
    assert "snapshot-for-" not in package["snapshot_id"]

    # The handoff carries the SAME reference to Archive: it is the one
    # context field Render reads for identity, and the custody block beside
    # it carries only Report-owned facts - never the overlaid identity.
    assert package["render_context"]["document_reference"] == expected_reference
    custody = package["render_context"]["archive"]
    assert custody["tenant_id"] == "tenant-sg"
    assert "document_reference" not in custody
    assert "snapshot_id" not in custody
    assert "declared_artifact_sha256" not in custody

    # Two render attempts of the same durable snapshot are the same governed
    # document; a different durable snapshot is not.
    second_attempt = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot=record.snapshot_payload,
        render_job_id="rdr_second_attempt",
        snapshot_id=record.snapshot_id,
    )
    assert second_attempt["render_context"]["document_reference"] == expected_reference
    other_snapshot = _build_render_package(
        job=ledger.get_job(ready.job_id),
        snapshot=record.snapshot_payload,
        render_job_id="rdr_third_attempt",
        snapshot_id="rsnap_regenerated",
    )
    assert other_snapshot["render_context"]["document_reference"] != expected_reference


def test_governed_rendering_fails_closed_without_a_durable_identity(tmp_path):
    """The synthetic fallback is gone: evidence without a durable identity is
    refused, never given a name Archive lineage cannot corroborate."""

    ledger, _store, ready = _seed_data_ready_job(tmp_path)

    with pytest.raises(ValueError, match="RENDER_PACKAGE_SNAPSHOT_IDENTITY_REQUIRED"):
        _build_render_package(
            job=ledger.get_job(ready.job_id),
            snapshot={"readiness": {"status": "ready"}},
            render_job_id="rdr_no_identity",
            snapshot_id="  ",
        )


@pytest.mark.asyncio
async def test_render_orchestration_marks_job_completed(tmp_path):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientSuccess(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    completed = await service.render_for_job(ready)

    assert completed.status == "archived"
    assert completed.render_job_id == f"rdr_{ready.job_id}_pdf"
    assert completed.render_artifact_sha256 == "sha256:artifact"
    assert completed.render_runtime_engine == "typst"
    assert completed.render_duration_ms == 812
    # The reconciliation identity is DERIVED (reference + exact digest) -
    # the same areq_ id lotus-render presented to Archive.
    assert completed.archive_request_id is not None
    assert completed.archive_request_id.startswith("areq_")
    assert completed.archive_document_id == "doc_archived"
    assert completed.archive_completed_at is not None
    assert [event.to_status for event in ledger.list_status_events(ready.job_id)] == [
        "accepted",
        "data_ready",
        "rendering",
        "completed",
        "archiving",
        "archived",
    ]


@pytest.mark.asyncio
async def test_render_orchestration_marks_validation_failure(tmp_path):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientFailure(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "render_validation_failed"
    assert failed.retry_eligible is False
    assert failed.render_job_id == f"rdr_{ready.job_id}_pdf"


@pytest.mark.asyncio
async def test_render_orchestration_skips_non_pdf_requests(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_job_request(requested_output_formats=["json"]),
        caller_context=_caller(),
        idempotency_key="idem-no-pdf",
    )
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientSuccess(),
        snapshot_store=object(),
        job_ledger=ledger,
    )

    returned = await service.render_for_job(job)

    assert returned.job_id == job.job_id
    assert returned.status == "accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", "accepted"])
async def test_render_orchestration_skips_jobs_that_are_not_data_ready_for_pdf(
    tmp_path,
    status,
):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    if status == "failed":
        job = ledger.mark_failed(
            job_id=ready.job_id,
            actor=ready.triggered_by,
            correlation_id=ready.correlation_id,
            trace_id=ready.trace_id,
            failure_category="render_execution_failed",
            failure_message="Render worker unavailable.",
            retry_eligible=True,
        )
    elif status == "cancelled":
        fresh = ledger.create_portfolio_review_job(
            request=_job_request(),
            caller_context=_caller(),
            idempotency_key="idem-render-cancelled-skip",
        )
        job = ledger.cancel_job(
            job_id=fresh.job_id,
            actor=fresh.triggered_by,
            correlation_id=fresh.correlation_id,
            trace_id=fresh.trace_id,
        )
    else:
        job = ledger.create_portfolio_review_job(
            request=_job_request(),
            caller_context=_caller(),
            idempotency_key="idem-render-accepted-skip",
        )

    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientSuccess(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    returned = await service.render_for_job(job)

    assert returned == job


@pytest.mark.asyncio
@pytest.mark.parametrize("restart_status", ["rendering", "completed", "archiving"])
async def test_render_orchestration_resumes_after_worker_restart(tmp_path, restart_status):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    rendering = ledger.mark_rendering(
        job_id=ready.job_id,
        actor=ready.triggered_by,
        correlation_id=ready.correlation_id,
        trace_id=ready.trace_id,
        render_job_id=f"rdr_{ready.job_id}_pdf",
        output_format="pdf",
        template_id="portfolio-review",
        template_version="v1",
    )
    restart_job = rendering
    if restart_status in {"completed", "archiving"}:
        restart_job = ledger.mark_completed(
            job_id=ready.job_id,
            actor=ready.triggered_by,
            correlation_id=ready.correlation_id,
            trace_id=ready.trace_id,
            render_job_id=f"rdr_{ready.job_id}_pdf",
            output_format="pdf",
            template_id="portfolio-review",
            template_version="v1",
            template_publication="development",
            artifact_sha256="sha256:artifact",
            bounded_determinism_fingerprint="fingerprint",
            runtime_engine="typst",
            runtime_engine_version="0.14.2",
            render_duration_ms=812,
        )
    if restart_status == "archiving":
        restart_job = ledger.mark_archiving(
            job_id=ready.job_id,
            actor=ready.triggered_by,
            correlation_id=ready.correlation_id,
            trace_id=ready.trace_id,
            archive_request_id=f"arch_rdr_{ready.job_id}_pdf",
        )
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientSuccess(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    resumed = await service.render_for_job(restart_job)

    assert resumed.status == "archived"
    assert resumed.archive_document_id == "doc_archived"


@pytest.mark.asyncio
async def test_render_orchestration_marks_conflict_failure(tmp_path):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientConflict(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "render_conflict"
    assert failed.retry_eligible is False


@pytest.mark.asyncio
async def test_render_orchestration_marks_retryable_execution_failure(tmp_path):
    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientServerError(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "render_execution_failed"
    assert failed.failure_message == "lotus-render unavailable"
    assert failed.retry_eligible is True


@pytest.mark.asyncio
async def test_artifactless_replay_still_records_verified_custody(tmp_path):
    """A "rendered" response without artifact bytes is a replay of a render
    that already completed. Under the #120 cutover Report never needed the
    bytes - Render delivered them to Archive during the original call - so
    the replay carries the terminal custody truth and the job archives, the
    lost-response window closed without regenerating anything."""

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientSuccessWithoutArtifact(),
        snapshot_store=store,
        job_ledger=ledger,
    )

    archived = await service.render_for_job(ready)

    assert archived.status == "archived"
    assert archived.archive_document_id == "doc_archived"
    assert [event.to_status for event in ledger.list_status_events(ready.job_id)] == [
        "accepted",
        "data_ready",
        "rendering",
        "completed",
        "archiving",
        "archived",
    ]


@pytest.mark.asyncio
async def test_the_source_owned_request_id_is_recorded_verbatim(tmp_path):
    """render#258: Render serializes the archive_request_id it presented to
    Archive; Report records that value verbatim - the local derivation is
    only a rollout fallback for responses predating the field."""

    class _RenderClientWithSourceOwnedId(_RenderClientWithArchiveOutcome):
        async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
            status_code, response = await super().submit_render_package(
                payload, correlation_id, trace_id
            )
            response["archive_request_id"] = "areq_source_owned_0123456789abcdef"
            return status_code, response

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientWithSourceOwnedId("archive_pending"),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.failure_category == "archive_outcome_unknown"
    assert failed.archive_request_id == "areq_source_owned_0123456789abcdef"


@pytest.mark.asyncio
async def test_a_custody_refusal_in_archives_own_words_is_terminal(tmp_path):
    """archive_detail carries Render's stable grammar; a 4xx refusal
    (checksum mismatch, identity collision) replays identically under the
    same declaration, so the job is terminal for every family until an
    operator acts - and the refusal words reach the failure message."""

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientWithArchiveOutcome(
            "archive_failed",
            archive_detail=(
                "archive_refused_422: declared_checksum_mismatch: declared "
                "artifact sha256 does not match computed"
            ),
        ),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "archive_handoff_failed"
    assert failed.retry_eligible is False
    assert "declared_checksum_mismatch" in (failed.failure_message or "")


@pytest.mark.asyncio
async def test_pending_custody_on_a_resumed_archiving_job_keeps_its_request_id(tmp_path):
    """A worker resuming an archiving-state job does not re-transition to
    archiving; the pending outcome still fails closed with the recorded
    reconciliation identity intact."""

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    ledger.mark_rendering(
        job_id=ready.job_id,
        actor=ready.triggered_by,
        correlation_id=ready.correlation_id,
        trace_id=ready.trace_id,
        render_job_id=f"rdr_{ready.job_id}_pdf",
        output_format="pdf",
        template_id="portfolio-review",
        template_version="v1",
    )
    ledger.mark_completed(
        job_id=ready.job_id,
        actor=ready.triggered_by,
        correlation_id=ready.correlation_id,
        trace_id=ready.trace_id,
        render_job_id=f"rdr_{ready.job_id}_pdf",
        output_format="pdf",
        template_id="portfolio-review",
        template_version="v1",
        template_publication="development",
        artifact_sha256="sha256:artifact",
        bounded_determinism_fingerprint="fingerprint",
        runtime_engine="typst",
        runtime_engine_version="0.14.2",
        render_duration_ms=812,
    )
    archiving = ledger.mark_archiving(
        job_id=ready.job_id,
        actor=ready.triggered_by,
        correlation_id=ready.correlation_id,
        trace_id=ready.trace_id,
        archive_request_id="areq_previously_recorded",
    )
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientWithArchiveOutcome("archive_pending"),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(archiving)

    assert failed.status == "failed"
    assert failed.failure_category == "archive_outcome_unknown"
    # COALESCE semantics: the previously recorded id survives the failure.
    assert failed.archive_request_id == "areq_previously_recorded"


@pytest.mark.asyncio
async def test_a_package_validation_error_fails_the_job_before_any_render_call(tmp_path):
    """The fail-closed identity guard surfaces through the ORCHESTRATION as
    render_validation_failed - not as an unhandled exception."""

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    record = store.get_snapshot_by_job(ready.job_id)

    class _BlankIdentityStore:
        def get_snapshot_by_job(self, report_job_id):
            class _Record:
                snapshot_id = "  "
                snapshot_hash = record.snapshot_hash
                snapshot_payload = record.snapshot_payload
                report_data_contract_version = record.report_data_contract_version
                report_revision_id = record.report_revision_id

            return _Record()

    class _RenderMustNotBeCalled:
        async def submit_render_package(self, payload, correlation_id=None, trace_id=None):
            raise AssertionError("a package that failed validation must not be submitted")

    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderMustNotBeCalled(),
        snapshot_store=_BlankIdentityStore(),
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "render_validation_failed"
    assert failed.retry_eligible is False
    assert "RENDER_PACKAGE_SNAPSHOT_IDENTITY_REQUIRED" in (failed.failure_message or "")


def test_custody_block_carries_every_optional_report_owned_fact(tmp_path):
    """client_reference, retention policy fields, and the advisor
    commentary summary all ride the custody block when the evidence
    carries them."""

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    snapshot = store.get_snapshot_by_job(ready.job_id)
    snapshot.snapshot_payload.setdefault("clientProfile", {}).setdefault("identity", {})[
        "client_reference"
    ] = "client-ref-042"
    snapshot.snapshot_payload["advisor_commentary_package"] = {
        "status": "included",
        "run_id": "run_042",
        "request_id": "req_042",
        "content_hash": "sha256:commentary",
        "review": {
            "review_event_id": "rev_042",
            "review_action": "APPROVE_FOR_CLIENT_USE",
        },
    }
    job = ledger.get_job(ready.job_id)
    job.options["retention_policy_id"] = "generated-report-standard"
    job.options["retain_until_date"] = "2033-04-22"

    package = _build_render_package(
        job=job,
        snapshot=snapshot.snapshot_payload,
        render_job_id="rdr_custody_optional",
        snapshot_id=snapshot.snapshot_id,
    )

    custody = package["render_context"]["archive"]
    assert custody["client_reference"] == "client-ref-042"
    assert custody["retention_policy_id"] == "generated-report-standard"
    assert custody["retain_until_date"] == "2033-04-22"
    assert custody["advisor_commentary"]["run_id"] == "run_042"


def test_package_builder_scalar_coercions_hold_their_edges():
    from app.reporting_render.package_builder import _optional_bool

    assert _optional_bool(True) is True
    assert _optional_bool("YES") is True
    assert _optional_bool("0") is False
    assert _optional_bool("maybe") is None
    assert _optional_bool(3.5) is None


@pytest.mark.asyncio
async def test_pending_custody_fails_closed_with_the_reconciliation_identity(tmp_path):
    """archive_pending means the delivery deadline expired after the request
    may have committed. The derived request id is recorded durably BEFORE the
    failure posture, so reconciliation survives a crash; the job fails closed
    retry-eligible - a retry re-renders deterministically and converges on
    the same custody record."""

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientWithArchiveOutcome("archive_pending"),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "archive_outcome_unknown"
    assert failed.retry_eligible is True
    assert failed.archive_request_id is not None
    assert failed.archive_request_id.startswith("areq_")
    assert failed.archive_request_id in (failed.failure_message or "")
    assert [event.to_status for event in ledger.list_status_events(ready.job_id)] == [
        "accepted",
        "data_ready",
        "rendering",
        "completed",
        "archiving",
        "failed",
    ]


@pytest.mark.asyncio
async def test_failed_handoff_fails_closed_and_stays_retryable(tmp_path):
    """archive_failed covers Archive refusing custody and Archive being
    unreachable; the render response does not yet carry which. Retrying is
    idempotent either way - redelivery converges, a refusal re-fails
    identically - so the posture stays retry-eligible with the caveat in the
    message."""

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientWithArchiveOutcome("archive_failed"),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "archive_handoff_failed"
    assert failed.retry_eligible is True
    # A lost connection or exhausted 5xx does not prove Archive failed to
    # commit: the exact delivery id is recorded durably so replay can
    # resolve it before rendering any replacement.
    assert failed.archive_request_id is not None
    assert failed.archive_request_id.startswith("areq_")


@pytest.mark.asyncio
async def test_a_null_archive_state_is_a_configuration_error_never_unarchived(tmp_path):
    """The byte relay is retired: there is no fallback delivery path. A
    rendered response with no archive handoff applied is a configuration
    error (Render's archive handoff is off) and the job fails closed - a
    governed document is never silently left out of custody."""

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientWithArchiveOutcome(None),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "archive_handoff_not_configured"
    assert failed.retry_eligible is True
    assert "no archive handoff applied" in (failed.failure_message or "")


@pytest.mark.asyncio
async def test_verified_custody_without_a_document_id_is_malformed_not_archived(tmp_path):
    """archived_verified without the durable document id cannot mark the job
    archived - the durable reference IS the evidence. A malformed response
    fails closed as unconfigured handoff rather than trusting the state word
    alone."""

    ledger, store, ready = _seed_data_ready_job(tmp_path)
    service = PortfolioReviewRenderOrchestrationService(
        render_client=_RenderClientWithArchiveOutcome("archived_verified"),
        snapshot_store=store,
        job_ledger=ledger,
    )

    failed = await service.render_for_job(ready)

    assert failed.status == "failed"
    assert failed.failure_category == "archive_handoff_not_configured"


def test_build_render_package_uses_fallback_values_for_sparse_snapshot(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-sparse",
    )

    payload = _build_render_package(
        job=job,
        snapshot={"overview": {"currency": "SGD", "total_market_value": "1000.50"}},
        render_job_id="rdr-sparse",
        snapshot_id="rsnap_test_durable",
    )

    # The synthetic snapshot-for-<job> fallback is gone: a governed document
    # never wears an identity Archive lineage will not record. Sparse
    # PAYLOADS still render; an absent durable identity fails closed instead.
    assert payload["snapshot_id"] == "rsnap_test_durable"
    assert payload["report_data"]["client_name"] == "Client"
    assert payload["report_data"]["currency"] == "SGD"
    assert payload["report_data"]["total_value"] == "1000.50"
    assert payload["report_data"]["review_period_label"] == "YTD"
    assert payload["report_data"]["performance_periods"] == []
    assert payload["report_data"]["top_holdings"] == []
    assert payload["report_data"]["review_observations"] == [
        "Portfolio review was rendered from the governed lotus-report snapshot."
    ]


def test_build_render_package_emits_richer_report_contract(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=_job_request(),
        caller_context=_caller(),
        idempotency_key="idem-rich",
    )

    payload = _build_render_package(
        job=job,
        render_job_id="rdr-rich",
        snapshot_id="rsnap_test_durable",
        snapshot={
            "portfolioName": "PB SG Global Balanced",
            "reviewPeriod": {"label": "YTD"},
            "readiness": {"status": "ready"},
            "reportingCurrency": "USD",
            "clientProfile": {
                "identity": {
                    "client_name": "Alex Tan",
                    "advisor_id": "RM_SG_001",
                    "booking_center_code": "Singapore",
                },
                "mandate_profile": {"risk_exposure": "balanced"},
            },
            "overview": {"currency": "USD", "total_market_value": "1000.50"},
            "allocation": {
                "byAssetClass": [
                    {
                        "group": "Equity",
                        "weight": 60.0,
                        "market_value": 600000.0,
                        "position_count": 3,
                    },
                    {
                        "group": "Fixed Income",
                        "weight": 35.0,
                        "market_value": 350000.0,
                        "position_count": 2,
                    },
                ],
                "byCurrency": [
                    {
                        "group": "USD",
                        "weight": 95.0,
                        "market_value": 950000.0,
                        "position_count": 5,
                    },
                    {
                        "group": "SGD",
                        "weight": 5.0,
                        "market_value": 50000.0,
                        "position_count": 1,
                    },
                ],
                "byRegion": [
                    {
                        "group": "North America",
                        "weight": 62.0,
                        "market_value": 620000.0,
                        "position_count": 4,
                    },
                    {
                        "group": "Asia",
                        "weight": 18.0,
                        "market_value": 180000.0,
                        "position_count": 1,
                    },
                ],
                "bySector": [
                    {
                        "group": "Technology",
                        "weight": 30.0,
                        "market_value": 300000.0,
                        "position_count": 2,
                    },
                    {
                        "group": "Healthcare",
                        "weight": 15.0,
                        "market_value": 150000.0,
                        "position_count": 1,
                    },
                ],
                "byCountry": [
                    {
                        "group": "United States",
                        "weight": 55.0,
                        "market_value": 550000.0,
                        "position_count": 3,
                    }
                ],
                "byProductType": [
                    {
                        "group": "Fund",
                        "weight": 95.0,
                        "market_value": 950000.0,
                        "position_count": 5,
                    }
                ],
                "byRating": [
                    {
                        "group": "A",
                        "weight": 40.0,
                        "market_value": 400000.0,
                        "position_count": 2,
                    }
                ],
            },
            "performance": {
                "summary": {
                    "YTD": {
                        "net_cumulative_return": 4.1,
                        "benchmark_cumulative_return": 3.4,
                        "benchmark_relative_return": 0.7,
                    }
                }
            },
            "riskAnalytics": {
                "summary": {
                    "YTD": {
                        "volatility": 12.0,
                        "beta": 0.82,
                        "tracking_error": 4.0,
                        "information_ratio": 0.72,
                        "value_at_risk": -2.0,
                    }
                }
            },
            "holdings": {
                "holdingsByAssetClass": {
                    "Equity": [
                        {
                            "security_id": "EQ-1",
                            "instrument_name": "Equity 1",
                            "isin": "US0000000001",
                            "quantity": 2200.0,
                            "position_date": "2026-04-22",
                            "product_type": "Fund",
                            "sector": "Technology",
                            "country_of_risk": "United States",
                            "rating": "A",
                            "liquidity_tier": "High",
                            "held_since_date": "2024-01-15",
                            "market_price": 102.35,
                            "cost_basis_reporting_currency": 500000.0,
                            "weight": 60.0,
                            "market_value_reporting_currency": 600000.0,
                            "unrealized_pnl_reporting_currency": 100000.0,
                            "unrealized_pnl_pct": 20.0,
                            "ytd_contribution_pct": 3.5,
                            "ytd_average_weight_pct": 55.0,
                            "ytd_total_return_pct": 8.4,
                            "currency": "USD",
                        }
                    ]
                }
            },
            "transactions": {
                "transactionsByCategory": {
                    "Trading": [
                        {
                            "transaction_id": "TXN-1",
                            "transaction_date": "2026-04-18",
                            "transaction_type": "SELL",
                            "instrument_id": "INST-1",
                            "security_id": "EQ-1",
                            "transaction_category": "Trading",
                            "display_label": "Sell",
                            "cash_leg": False,
                            "asset_class": "Equity",
                            "amount_reporting_currency": 25000.0,
                            "gross_transaction_amount_reporting_currency": 25000.0,
                            "realized_pnl_reporting_currency": 1250.0,
                            "realized_pnl_local": 1250.0,
                            "net_interest_amount_reporting_currency": 0.0,
                            "withholding_tax_amount_reporting_currency": 0.0,
                            "income_or_tax_reporting_currency": 0.0,
                        }
                    ]
                },
                "transactionCount": 1,
            },
            "reviewObservations": [
                {"summary": "Risk posture remained within the balanced mandate range."}
            ],
            "evidence": {
                "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
                "trust_metadata": {
                    "completeness_status": "complete",
                    "data_quality_status": "quality_passed",
                },
            },
            "keyFigures": {
                "client_profile": {
                    "objective": (
                        "Long-term real wealth growth with controlled income and liquidity."
                    )
                },
                "portfolio_value": {
                    "invested_market_value_reporting_currency": 998800.0,
                    "cash_balance_reporting_currency": 50000.0,
                    "cash_weight_pct": 5.0,
                },
                "allocation": {
                    "name": "Equity",
                    "weight_pct": 60.0,
                    "market_value_reporting_currency": 600000.0,
                    "position_count": 3,
                },
                "performance": {
                    "benchmark_comparison_status": "available",
                    "largest_positive_contributor": {
                        "security_name": "Equity 1",
                        "total_contribution_pct": 3.5,
                    },
                },
                "risk": {
                    "ytd_volatility_pct": 12.0,
                    "ytd_beta": 0.82,
                    "ytd_tracking_error_pct": 4.0,
                    "ytd_information_ratio": 0.72,
                },
                "holdings": {"position_count": 12},
            },
        },
    )

    report_data = payload["report_data"]
    assert report_data["portfolio_name"] == "PB SG Global Balanced"
    assert report_data["summary_paragraph"] == (
        "Risk posture remained within the balanced mandate range."
    )
    assert report_data["mandate"] == {
        "objective": "Long-term real wealth growth with controlled income and liquidity.",
        "risk_exposure": "balanced",
        "booking_center_code": "Singapore",
        "advisor_id": "RM_SG_001",
    }
    assert report_data["portfolio_metrics"] == {
        "invested_value": "998800.00",
        "cash_balance": "50000.00",
        "cash_weight_pct": "5.00%",
    }
    assert report_data["allocation_summary"] == {
        "largest_asset_class_name": "Equity",
        "largest_asset_class_weight_pct": "60.00%",
        "largest_asset_class_market_value": "600000.00",
        "largest_asset_class_position_count": 3,
    }
    assert report_data["allocation_breakdowns"] == {
        "by_asset_class": [
            {
                "name": "Equity",
                "weight_pct": "60.00%",
                "market_value": "600000.00",
                "position_count": 3,
            },
            {
                "name": "Fixed Income",
                "weight_pct": "35.00%",
                "market_value": "350000.00",
                "position_count": 2,
            },
        ],
        "by_currency": [
            {
                "name": "USD",
                "weight_pct": "95.00%",
                "market_value": "950000.00",
                "position_count": 5,
            },
            {
                "name": "SGD",
                "weight_pct": "5.00%",
                "market_value": "50000.00",
                "position_count": 1,
            },
        ],
        "by_region": [
            {
                "name": "North America",
                "weight_pct": "62.00%",
                "market_value": "620000.00",
                "position_count": 4,
            },
            {
                "name": "Asia",
                "weight_pct": "18.00%",
                "market_value": "180000.00",
                "position_count": 1,
            },
        ],
        "by_sector": [
            {
                "name": "Technology",
                "weight_pct": "30.00%",
                "market_value": "300000.00",
                "position_count": 2,
            },
            {
                "name": "Healthcare",
                "weight_pct": "15.00%",
                "market_value": "150000.00",
                "position_count": 1,
            },
        ],
        "by_country": [
            {
                "name": "United States",
                "weight_pct": "55.00%",
                "market_value": "550000.00",
                "position_count": 3,
            }
        ],
        "by_product_type": [
            {
                "name": "Fund",
                "weight_pct": "95.00%",
                "market_value": "950000.00",
                "position_count": 5,
            }
        ],
        "by_rating": [
            {
                "name": "A",
                "weight_pct": "40.00%",
                "market_value": "400000.00",
                "position_count": 2,
            }
        ],
    }
    assert report_data["performance_periods"] == [
        {
            "period": "YTD",
            "net_return_pct": "4.10%",
            "benchmark_return_pct": "3.40%",
            "relative_return_pct": "0.70%",
        }
    ]
    assert report_data["transaction_period_label"] == "From 01.01.2026 to 22.04.2026"
    assert report_data["risk_summary"] == {
        "volatility_pct": "12.00%",
        "beta": "0.82",
        "tracking_error_pct": "4.00%",
        "information_ratio": "0.72",
        "value_at_risk_pct": "-2.00%",
        # Both captured on every risk call and discarded at this boundary
        # until #235. This fixture states neither, so they read as absent -
        # which is itself the point: the fixture was built from what the
        # package already forwarded, so it could never fail when the boundary
        # dropped something.
        "drawdown_pct": "Not available",
        "expected_shortfall_pct": "Not available",
    }
    assert report_data["top_holdings"] == [
        {
            "asset_class": "Equity",
            "security_name": "Equity 1",
            "weight_pct": "60.00%",
            "quantity": "2200.00",
            "currency": "USD",
            "security_id": "EQ-1",
            "instrument_name": "Equity 1",
            "isin": "US0000000001",
            "position_date": "2026-04-22",
            "product_type": "Fund",
            "sector": "Technology",
            "country_of_risk": "United States",
            "rating": "A",
            "liquidity_tier": "High",
            "held_since_date": "2024-01-15",
            "market_price": "102.35",
            "cost_basis_reporting_currency": "500000.00",
            "cost_basis_local": "Not available",
            "market_value": "600000.00",
            "market_value_local": "Not available",
            "unrealized_pnl": "100000.00",
            "unrealized_pnl_local": "Not available",
            "unrealized_pnl_pct": "20.00%",
            "ytd_contribution_pct": "3.50%",
            "ytd_average_weight_pct": "55.00%",
            "ytd_total_return_pct": "8.40%",
        }
    ]
    assert report_data["positions"] == report_data["top_holdings"]
    assert report_data["transactions"] == [
        {
            "category": "Trading",
            "asset_class": "Equity",
            "transaction_category": "Trading",
            "display_label": "Sell",
            "cash_leg": "No",
            "transaction_id": "TXN-1",
            "trade_date": "2026-04-18",
            "transaction_type": "SELL",
            "instrument_id": "INST-1",
            "security_id": "EQ-1",
            "amount": "25000.00",
            "gross_amount_reporting_currency": "25000.00",
            "realized_pnl_reporting_currency": "1250.00",
            "realized_pnl_local": "1250.00",
            "net_interest_amount_reporting_currency": "0.00",
            "withholding_tax_amount_reporting_currency": "0.00",
            "income_or_tax_reporting_currency": "0.00",
        }
    ]
    assert report_data["governance_summary"] == {
        "source_services": ["lotus-core", "lotus-performance", "lotus-risk"],
        "completeness_status": "complete",
        "data_quality_status": "quality_passed",
        "readiness_status": "ready",
    }


def test_build_render_package_emits_outcome_review_contract(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_outcome_review_report_job(
        request=OutcomeReviewReportJobRequest.model_validate(
            {
                "outcome_report_input": {
                    "contract_version": "1.0",
                    "outcome_review_id": "dor_001",
                    "outcome_review_content_hash": "sha256:outcome-review",
                    "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                    "proof_pack_id": "dpp_001",
                    "rebalance_run_id": "run_001",
                    "wave_id": "wave_001",
                    "review_window": {"start_date": "2026-04-22", "end_date": "2026-04-23"},
                    "report_title": "Post-Trade Outcome Review - PB_SG_GLOBAL_BAL_001",
                    "state": "READY",
                    "generated_at": "2026-04-23T09:00:00Z",
                    "overall_outcome": "Execution outcome aligned with pre-trade proof.",
                    "supportability": {"state": "READY", "reason_codes": ["outcome_ready"]},
                    "dimensions": [
                        {
                            "dimension": "PERFORMANCE",
                            "state": "READY",
                            "reason_code": "performance_realized",
                            "expected": "4.10",
                            "realized": "4.22",
                            "variance": "0.12",
                            "explanation": "Realized performance exceeded expected performance.",
                        }
                    ],
                    "source_lineage": [
                        {
                            "source_system": "lotus-manage",
                            "source_type": "DPM_OUTCOME_REPORT_INPUT",
                            "source_id": "dor_001:dpm_outcome_report_input",
                            "content_hash": "sha256:report-input",
                        }
                    ],
                    "source_hashes": {"realized": "sha256:realized"},
                    "section_hashes": {"proof_pack": "sha256:proof-pack"},
                    "content_hash": "sha256:report-input",
                    "redaction_policy": "NO_RAW_PAYLOADS",
                    "retention_policy": "generated-report-standard",
                    "evidence_ref": {
                        "source_system": "lotus-manage",
                        "source_type": "DPM_OUTCOME_REPORT_INPUT",
                        "source_id": "dor_001:dpm_outcome_report_input",
                        "content_hash": "sha256:report-input",
                    },
                    "portfolio_memory_context": _portfolio_memory_context(),
                },
                "requested_output_formats": ["pdf"],
            }
        ),
        caller_context=_caller(),
        idempotency_key="idem-outcome-render",
    )

    payload = _build_render_package(
        job=job,
        snapshot=job.options["outcome_report_input"],
        render_job_id="rdr-outcome",
        snapshot_id="rsnap_test_durable",
    )

    assert payload["report_type"] == "outcome_review"
    assert payload["report_data_contract_version"] == "dpm_outcome_report_input.v1"
    assert payload["template_id"] == "outcome-review"
    assert payload["report_data"]["outcome_review_id"] == "dor_001"
    assert payload["report_data"]["portfolio_id"] == "PB_SG_GLOBAL_BAL_001"
    assert payload["report_data"]["dimensions"][0]["variance"] == "0.12"
    assert payload["report_data"]["portfolio_memory"]["status"] == "supplied"
    assert payload["report_data"]["portfolio_memory"]["event_count"] == 3
    assert payload["report_data"]["portfolio_memory"]["event_refs"][0]["event_type"] == (
        "PROOF_PACK_CREATED"
    )
    _assert_portfolio_memory_controls(payload["report_data"]["portfolio_memory"])
    assert payload["lineage_refs"] == [
        job.job_id,
        "dor_001",
        "sha256:report-input",
        "sha256:portfolio-memory",
    ]


def test_build_render_package_emits_proof_pack_contract(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_proof_pack_report_job(
        request=ProofPackReportJobRequest.model_validate(
            {
                "proof_pack_report_input": {
                    **_proof_pack_report_input(),
                    "portfolio_memory_context": _portfolio_memory_context(),
                },
                "requested_output_formats": ["pdf"],
            }
        ),
        caller_context=_caller(),
        idempotency_key="idem-proof-pack-render",
    )

    payload = _build_render_package(
        job=job,
        snapshot=job.options["proof_pack_report_input"],
        render_job_id="rdr-proof-pack",
        snapshot_id="rsnap_test_durable",
    )

    assert payload["report_type"] == "proof_pack"
    assert payload["report_data_contract_version"] == "dpm_proof_pack_report_input.v1"
    assert payload["template_id"] == "proof-pack"
    assert payload["disclosure_refs"] == ["proof-pack.standard-disclosures.v1"]
    assert payload["report_data"]["proof_pack_id"] == "dpp_001"
    assert payload["report_data"]["portfolio_id"] == "PB_SG_GLOBAL_BAL_001"
    assert payload["report_data"]["proof_pack_content_hash"] == "sha256:proof-pack"
    assert payload["report_data"]["sections"][0]["title"] == "Mandate context"
    assert payload["report_data"]["portfolio_memory"]["governance_policy"]["audit_policy"] == (
        "AUDIT_READ_AND_EXPORT"
    )
    _assert_portfolio_memory_controls(payload["report_data"]["portfolio_memory"])
    assert payload["lineage_refs"] == [
        job.job_id,
        "dpp_001",
        "sha256:report-input",
        "sha256:portfolio-memory",
    ]


def test_build_render_package_emits_wave_contract(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_wave_report_job(
        request=WaveReportJobRequest.model_validate(
            {
                "wave_report_input": {
                    "contract_version": "1.0",
                    "wave_id": "dwv_001",
                    "wave_content_hash": "sha256:wave",
                    "wave_state": "HANDOFF_READY",
                    "trigger_type": "EXPLICIT_PORTFOLIO_LIST",
                    "trigger_id": "manual-wave-001",
                    "trigger_rationale": "Review explicit affected portfolio list.",
                    "as_of_date": "2026-05-03",
                    "generated_at": "2026-05-03T09:00:00Z",
                    "report_title": "Rebalance Wave Evidence - dwv_001",
                    "aggregate_metrics": {"item_count": 1, "state_counts": {"HANDOFF_READY": 1}},
                    "supportability": {"supportability_state": "ready"},
                    "proof_pack_posture": {"ready_proof_pack_count": 1},
                    "items": [
                        {
                            "wave_item_id": "dwi_001",
                            "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                            "mandate_id": "MANDATE_PB_SG_GLOBAL_BAL_001",
                            "state": "HANDOFF_READY",
                            "reason_codes": ["WAVE_ITEM_HANDOFF_READY"],
                            "proof_pack_id": "dpp_001",
                            "proof_pack_state": "READY",
                        }
                    ],
                    "events": [
                        {
                            "event_type": "STATE_TRANSITION",
                            "to_state": "HANDOFF_READY",
                            "actor_id": "pm_001",
                            "reason_code": "WAVE_HANDOFF_READY",
                            "created_at": "2026-05-03T09:00:00Z",
                        }
                    ],
                    "handoff_refs": [{"handoff_ref_id": "dwh_001"}],
                    "external_execution_claimed": False,
                    "content_hash": "sha256:report-input",
                    "redaction_policy": "NO_RAW_PAYLOADS",
                    "retention_policy": "generated-report-standard",
                    "source_refs": [
                        {
                            "source_system": "lotus-manage",
                            "source_type": "DPM_WAVE_REPORT_INPUT",
                            "source_id": "dwv_001:dpm_wave_report_input",
                            "content_hash": "sha256:report-input",
                        }
                    ],
                    "evidence_ref": {
                        "source_system": "lotus-manage",
                        "ref_type": "DPM_WAVE_REPORT_INPUT",
                        "ref_id": "dwv_001:dpm_wave_report_input",
                        "content_hash": "sha256:report-input",
                    },
                    "portfolio_memory_context": _portfolio_memory_context(),
                },
                "requested_output_formats": ["pdf"],
            }
        ),
        caller_context=_caller(),
        idempotency_key="idem-wave-render",
    )

    payload = _build_render_package(
        job=job,
        snapshot=job.options["wave_report_input"],
        render_job_id="rdr-wave",
        snapshot_id="rsnap_test_durable",
    )

    assert payload["report_type"] == "rebalance_wave"
    assert payload["report_data_contract_version"] == "dpm_wave_report_input.v1"
    assert payload["template_id"] == "rebalance-wave"
    assert payload["disclosure_refs"] == ["rebalance-wave.standard-disclosures.v1"]
    assert payload["report_data"]["wave_id"] == "dwv_001"
    assert payload["report_data"]["proof_pack_posture"]["ready_proof_pack_count"] == 1
    assert payload["report_data"]["items"][0]["proof_pack_id"] == "dpp_001"
    assert payload["report_data"]["handoff_count"] == 1
    assert payload["report_data"]["external_execution_claimed"] is False
    assert payload["report_data"]["portfolio_memory"]["content_hash"] == "sha256:portfolio-memory"
    _assert_portfolio_memory_controls(payload["report_data"]["portfolio_memory"])
    assert payload["lineage_refs"] == [
        job.job_id,
        "dwv_001",
        "sha256:report-input",
        "sha256:portfolio-memory",
    ]


def test_build_render_package_rejects_incomplete_proof_pack_source_evidence(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=PortfolioReviewJobRequest.model_validate(
            {
                "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
                "as_of_date": "2026-05-03",
                "requested_output_formats": ["pdf"],
            }
        ),
        caller_context=_caller(),
        idempotency_key="idem-proof-pack-render-rejects-incomplete",
    )
    job = job.model_copy(
        update={
            "report_type": "proof_pack",
            "requested_output_formats": ["pdf"],
            "as_of_date": date(2026, 5, 3),
        }
    )

    with pytest.raises(ValueError, match="proof_pack_report_input.content_hash is required"):
        _build_render_package(
            job=job,
            snapshot={
                "proof_pack_id": "dpp_minimal",
                "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                "as_of_date": "2026-05-03",
            },
            render_job_id="rdr-proof-pack-fallbacks",
            snapshot_id="rsnap_test_durable",
        )


def test_build_render_package_rejects_incomplete_outcome_review_source_evidence(tmp_path):
    ledger = ReportJobLedger(tmp_path / "jobs.sqlite3")
    job = ledger.create_portfolio_review_job(
        request=PortfolioReviewJobRequest.model_validate(
            {
                "portfolio_scope": {"portfolio_ids": ["PB_SG_GLOBAL_BAL_001"]},
                "as_of_date": "2026-04-23",
                "requested_output_formats": ["pdf"],
            }
        ),
        caller_context=_caller(),
        idempotency_key="idem-outcome-render-rejects-incomplete",
    )
    job = job.model_copy(
        update={
            "report_type": "outcome_review",
            "requested_output_formats": ["pdf"],
            "as_of_date": date(2026, 4, 23),
        }
    )

    with pytest.raises(ValueError, match="outcome_report_input.content_hash is required"):
        _build_render_package(
            job=job,
            snapshot={
                "outcome_review_id": "dor_minimal",
                "portfolio_id": "PB_SG_GLOBAL_BAL_001",
                "review_window": {"end_date": "2026-04-23"},
            },
            render_job_id="rdr-outcome-fallbacks",
            snapshot_id="rsnap_test_durable",
        )


def test_render_service_helpers_cover_fallback_branches(monkeypatch):
    assert (
        _performance_observation(
            {
                "largest_positive_contributor": {
                    "security_name": "Lotus Global Equity Fund",
                    "ytd_contribution_pct": 1.237,
                }
            }
        )
        == "Lotus Global Equity Fund was the largest positive contributor "
        "at 1.24% YTD contribution."
    )
    assert _risk_observation({"ytd_volatility_pct": 7.891, "ytd_beta": 0.92}) == (
        "YTD volatility is 7.89% and beta is 0.92."
    )
    assert _performance_observation({"benchmark_comparison_status": "not_available"}) == (
        "Benchmark comparison status is not_available in the governed report snapshot."
    )
    assert _risk_observation({}) is None
    assert _holding_observation({"position_count": "7"}) == (
        "The report includes 7 sourced portfolio positions."
    )
    assert _optional_str("  trimmed  ") == "trimmed"
    assert _optional_str("   ") is None
    assert _optional_decimal(True) is None
    assert _optional_decimal(5) is not None
    assert _optional_decimal(Decimal("1.23")) == Decimal("1.23")
    assert _optional_decimal("bad-decimal") is None
    assert _optional_int(True) == 1
    assert _optional_int("bad-int") is None


def test_render_package_helpers_ignore_malformed_collection_rows(monkeypatch):
    assert (
        _performance_history(
            {"performance": {"monthly_history": "not-a-list"}}, "monthly_history", limit=3
        )
        == []
    )
    assert _performance_history(
        {"performance": {"monthly_history": [{"period": "2026-04"}, "bad-row"]}},
        "monthly_history",
        limit=3,
    ) == [
        {
            "period": "2026-04",
            "period_start": "Not available",
            "period_end": "Not available",
            "final_value": "Not available",
            "inflows": "Not available",
            "outflows": "Not available",
            "performance_value": "Not available",
            "cumulative_performance_value": "Not available",
            "twr_pct": "Not available",
            "cumulative_twr_pct": "Not available",
        }
    ]
    assert _transactions({"transactions": {"transactionsByCategory": {}}}) == []
    assert _positions({"holdings": {"holdingsByAssetClass": {"Equity": "not-a-list"}}}) == []
    assert _positions({"holdings": {"holdingsByAssetClass": {"Equity": ["bad-row"]}}}) == []
    assert (
        _transactions(
            {
                "transactions": {
                    "transactionsByAssetClass": {
                        "Equity": [
                            "bad-row",
                            {
                                "transaction_id": "TXN-1",
                                "transaction_date": "2026-04-22",
                            },
                        ],
                        "Cash": "bad-group",
                    }
                }
            }
        )[0]["category"]
        == "Equity"
    )
    assert _allocation_bucket_rows("bad-buckets") == []
    assert _allocation_bucket_rows(
        [
            "bad-row",
            {"group": "Cash", "weight": "5", "market_value": "50", "position_count": "1"},
            {"group": "Equity", "weight": "95", "market_value": "950", "position_count": 5},
        ]
    ) == [
        {
            "name": "Equity",
            "weight_pct": "95.00%",
            "market_value": "950.00",
            "position_count": 5,
        },
        {
            "name": "Cash",
            "weight_pct": "5.00%",
            "market_value": "50.00",
            "position_count": 1,
        },
    ]
    assert _date_text("2026-04-22") == "2026-04-22"
    with pytest.raises(ValueError, match="date value is required"):
        _date_text(None)

    class _SentinelClient:
        pass

    sentinel_client = _SentinelClient()
    sentinel_store = object()
    sentinel_ledger = object()

    monkeypatch.setattr(render_service, "RenderClient", lambda **kwargs: sentinel_client)
    monkeypatch.setattr(render_service, "get_report_input_snapshot_store", lambda: sentinel_store)
    monkeypatch.setattr(render_service, "get_report_job_ledger", lambda: sentinel_ledger)
    render_service.get_portfolio_review_render_orchestration_service.cache_clear()
    try:
        service = render_service.get_portfolio_review_render_orchestration_service()
    finally:
        render_service.get_portfolio_review_render_orchestration_service.cache_clear()

    assert service._render_client is sentinel_client
    assert service._snapshot_store is sentinel_store
    assert service._job_ledger is sentinel_ledger
