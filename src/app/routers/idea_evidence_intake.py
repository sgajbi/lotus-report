from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.config import settings
from app.idea_evidence_intake.models import (
    IdeaEvidenceMaterializationRecoveryIdentity,
    IdeaEvidencePackIntakeRequest,
    IdeaEvidencePackIntakeResponse,
    IdeaEvidencePackMaterializationRequest,
    IdeaEvidencePackMaterializationResponse,
)
from app.idea_evidence_intake.recovery import (
    IdeaMaterializationIdentityConflictError,
    IdeaMaterializationNotFoundError,
    recover_idea_materialization,
    recovery_identity_from_request,
)
from app.idea_evidence_intake.retention_policy import (
    IdeaEvidenceRetentionPolicy,
    IdeaEvidenceRetentionPolicyResolver,
    InMemoryIdeaEvidenceRetentionPolicyRegistry,
    RetentionPolicyError,
)
from app.idea_evidence_intake.service import (
    IdeaEvidenceIntakeConflictError,
    IdeaEvidenceIntakeLedger,
    build_proof_pack_report_job_request_from_idea_evidence,
)
from app.reporting_jobs.ledger import (
    IdempotencyConflictError,
    MissingIdempotencyKeyError,
    ReportJobLedger,
)
from app.reporting_jobs.models import ReportJobLedgerRecord
from app.reporting_jobs.service import get_report_job_ledger
from app.reporting_lineage.service import get_portfolio_review_snapshot_capture_service
from app.reporting_metrics import record_report_operation
from app.reporting_render.service import get_portfolio_review_render_orchestration_service
from app.routers.caller_context import caller_context_from_headers
from app.routers.report_ordering_validation import enforce_report_ordering_submission

router = APIRouter(prefix="/reports/idea-evidence-packs", tags=["Report Evidence"])


@lru_cache(maxsize=1)
def get_idea_evidence_intake_ledger() -> IdeaEvidenceIntakeLedger:
    return IdeaEvidenceIntakeLedger(Path(settings.idea_evidence_intake_ledger_path))


@lru_cache(maxsize=1)
def get_idea_evidence_retention_policy_resolver() -> IdeaEvidenceRetentionPolicyResolver:
    return InMemoryIdeaEvidenceRetentionPolicyRegistry()


def _resolve_retention_policy(
    *,
    resolver: IdeaEvidenceRetentionPolicyResolver,
    policy_ref: str,
    tenant_id: str,
    producer: str,
) -> IdeaEvidenceRetentionPolicy:
    try:
        return resolver.resolve(
            policy_ref=policy_ref,
            tenant_id=tenant_id,
            producer=producer,
        )
    except RetentionPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.post(
    "",
    response_model=IdeaEvidencePackIntakeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Accept reviewed lotus-idea evidence-pack intake",
    description=(
        "Accepts a source-safe, reviewed lotus-idea evidence-pack handoff for report-side "
        "intake tracking. This route proves only report-owned intake-route existence. It does not "
        "create a report job, render output, archive record, client publication authority, "
        "suitability decision, advisory proposal, execution workflow, or supported feature."
    ),
    responses={
        400: {
            "description": "Returned when the required idempotency key is missing.",
        },
        409: {
            "description": "Returned when the same idempotency key is replayed with new content.",
        },
    },
)
async def accept_idea_evidence_pack(
    request: IdeaEvidencePackIntakeRequest,
    ledger: IdeaEvidenceIntakeLedger = Depends(get_idea_evidence_intake_ledger),
    retention_policy_resolver: IdeaEvidenceRetentionPolicyResolver = Depends(
        get_idea_evidence_retention_policy_resolver
    ),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="Idempotency key for the intake handoff."),
    ] = None,
    actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    caller_application: Annotated[str | None, Header(alias="X-Caller-Application")] = None,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    region: Annotated[str | None, Header(alias="X-Region")] = None,
    booking_center_code: Annotated[str | None, Header(alias="X-Booking-Center-Code")] = None,
    role: Annotated[str | None, Header(alias="X-Role")] = None,
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
    trace_id: Annotated[str | None, Header(alias="X-Trace-ID")] = None,
) -> IdeaEvidencePackIntakeResponse:
    caller_context = caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=booking_center_code,
        role=role,
        correlation_id=correlation_id,
        trace_id=trace_id,
    )
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "missing_idempotency_key",
                "message": "Idempotency-Key header is required.",
            },
        )
    _resolve_retention_policy(
        resolver=retention_policy_resolver,
        policy_ref=request.retention_policy_ref,
        tenant_id=caller_context.tenant_id,
        producer=request.producer,
    )
    try:
        return ledger.accept(
            request,
            idempotency_key=idempotency_key.strip(),
            correlation_id=correlation_id,
            trace_id=trace_id,
            caller_context=caller_context,
        )
    except IdeaEvidenceIntakeConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idea_evidence_intake_conflict",
                "message": "Idea evidence intake idempotency key was replayed with new content.",
            },
        ) from exc


@router.post(
    "/materializations",
    response_model=IdeaEvidencePackMaterializationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Materialize reviewed lotus-idea evidence into a governed report job",
    description=(
        "Creates or reuses a report-owned proof-pack report job from reviewed lotus-idea evidence. "
        "The route persists an immutable snapshot, invokes the existing render/archive pipeline "
        "when PDF output is requested, and preserves lotus-idea as the evidence source authority. "
        "It does not grant suitability, execution, client-publication, distribution, or "
        "supported-feature authority."
    ),
    responses={
        400: {
            "description": "Returned when the required idempotency key is missing.",
        },
        409: {
            "description": "Returned when the same idempotency key is replayed with new content.",
        },
        422: {
            "description": (
                "Returned when the report output selection or retention policy is not available."
            ),
        },
    },
)
async def materialize_idea_evidence_pack(
    request: IdeaEvidencePackMaterializationRequest,
    ledger: ReportJobLedger = Depends(get_report_job_ledger),
    intake_ledger: IdeaEvidenceIntakeLedger = Depends(get_idea_evidence_intake_ledger),
    retention_policy_resolver: IdeaEvidenceRetentionPolicyResolver = Depends(
        get_idea_evidence_retention_policy_resolver
    ),
    capture_service: object = Depends(get_portfolio_review_snapshot_capture_service),
    render_service: object = Depends(get_portfolio_review_render_orchestration_service),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="Idempotency key for materialization."),
    ] = None,
    actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    caller_application: Annotated[str | None, Header(alias="X-Caller-Application")] = None,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    region: Annotated[str | None, Header(alias="X-Region")] = None,
    booking_center_code: Annotated[str | None, Header(alias="X-Booking-Center-Code")] = None,
    role: Annotated[str | None, Header(alias="X-Role")] = None,
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
    trace_id: Annotated[str | None, Header(alias="X-Trace-ID")] = None,
) -> IdeaEvidencePackMaterializationResponse:
    started_at = perf_counter()
    caller_context = caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=booking_center_code,
        role=role,
        correlation_id=correlation_id,
        trace_id=trace_id,
    )
    if caller_context.caller_application != "lotus-idea":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "idea_materialization_forbidden",
                "message": "Only the admitted lotus-idea service may materialize Idea evidence.",
            },
        )
    if not idempotency_key or not idempotency_key.strip():
        record_report_operation(
            operation="report_job_submission",
            status="failed",
            failure_category="missing_idempotency_key",
            duration_seconds=perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "missing_idempotency_key",
                "message": "Idempotency-Key header is required.",
            },
        )
    enforce_report_ordering_submission(
        report_family_id="proof_pack",
        ordering_mode_id="source_workflow",
        requested_output_formats=request.requested_output_formats,
        options=request.options,
    )
    materialization_key = idempotency_key.strip()
    retention_policy = _resolve_retention_policy(
        resolver=retention_policy_resolver,
        policy_ref=request.idea_evidence_pack.retention_policy_ref,
        tenant_id=caller_context.tenant_id,
        producer=request.idea_evidence_pack.producer,
    )
    try:
        intake_ledger.accept(
            request.idea_evidence_pack,
            idempotency_key=materialization_key,
            correlation_id=correlation_id,
            trace_id=trace_id,
            caller_context=caller_context,
        )
        report_job_request = build_proof_pack_report_job_request_from_idea_evidence(
            request,
            retention_policy=retention_policy,
        )
        record = ledger.create_proof_pack_report_job(
            request=report_job_request,
            caller_context=caller_context,
            idempotency_key=materialization_key,
        )
    except (IdeaEvidenceIntakeConflictError, IdempotencyConflictError) as exc:
        record_report_operation(
            operation="report_job_submission",
            status="failed",
            failure_category="idempotency_conflict",
            duration_seconds=perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_conflict",
                "message": "Idempotency-Key was reused with different idea evidence content.",
            },
        ) from exc
    except MissingIdempotencyKeyError as exc:
        record_report_operation(
            operation="report_job_submission",
            status="failed",
            failure_category="missing_idempotency_key",
            duration_seconds=perf_counter() - started_at,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "missing_idempotency_key",
                "message": "Idempotency-Key header is required.",
            },
        ) from exc
    if record.status == "accepted":
        record = await capture_service.capture_for_job(record)  # type: ignore[attr-defined]
    if record.status == "data_ready" and "pdf" in report_job_request.requested_output_formats:
        record = await render_service.render_for_job(record)  # type: ignore[attr-defined]
    record_report_operation(
        operation="report_job_submission",
        status=record.status,
        failure_category=record.failure_category,
        duration_seconds=perf_counter() - started_at,
    )
    return _materialization_response(
        ledger=ledger,
        record=record,
        request=request,
        idempotency_key=record.idempotency_key,
    )


@router.get(
    "/materializations",
    response_model=IdeaEvidencePackMaterializationResponse,
    summary="Recover one exact lotus-idea materialization receipt",
    description=(
        "Returns the current Report-owned receipt for one tenant-scoped idempotent Idea "
        "materialization after an uncertain response. Every Idea and portfolio identity is "
        "matched before a receipt is returned. This read never creates or retries a report job."
    ),
    responses={
        403: {"description": "The caller is not the admitted lotus-idea recovery consumer."},
        404: {"description": "No materialization exists in the caller's tenant scope."},
        409: {"description": "Stored and expected materialization identity do not match."},
        422: {"description": "The required exact recovery identity is missing or malformed."},
    },
)
async def recover_idea_evidence_pack_materialization(
    idempotency_key: Annotated[
        str,
        Query(
            alias="idempotencyKey",
            min_length=1,
            description="Original materialization command idempotency key.",
            examples=["idea-report-materialization-001"],
        ),
    ],
    report_evidence_pack_id: Annotated[
        str,
        Query(
            alias="reportEvidencePackId",
            min_length=3,
            description="Exact lotus-idea report evidence-pack identifier.",
            examples=["irep_001"],
        ),
    ],
    conversion_intent_id: Annotated[
        str,
        Query(
            alias="conversionIntentId",
            min_length=3,
            description="Exact governed conversion-intent identifier.",
            examples=["icnv_001"],
        ),
    ],
    candidate_id: Annotated[
        str,
        Query(
            alias="candidateId",
            min_length=3,
            description="Exact reviewed opportunity-candidate identifier.",
            examples=["icand_001"],
        ),
    ],
    evidence_packet_id: Annotated[
        str,
        Query(
            alias="evidencePacketId",
            min_length=3,
            description="Exact reviewed evidence-packet identifier.",
            examples=["ievp_001"],
        ),
    ],
    evidence_content_fingerprint: Annotated[
        str,
        Query(
            alias="evidenceContentFingerprint",
            pattern=r"^sha256:[a-zA-Z0-9_.:-]+$",
            description="Exact source evidence content fingerprint.",
            examples=["sha256:idea-evidence-content"],
        ),
    ],
    portfolio_id: Annotated[
        str,
        Query(
            alias="portfolioId",
            min_length=3,
            description="Exact portfolio scope accepted with the Report request.",
            examples=["PB_SG_GLOBAL_BAL_001"],
        ),
    ],
    ledger: ReportJobLedger = Depends(get_report_job_ledger),
    actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    caller_application: Annotated[str | None, Header(alias="X-Caller-Application")] = None,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    region: Annotated[str | None, Header(alias="X-Region")] = None,
    booking_center_code: Annotated[str | None, Header(alias="X-Booking-Center-Code")] = None,
    role: Annotated[str | None, Header(alias="X-Role")] = None,
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
    trace_id: Annotated[str | None, Header(alias="X-Trace-ID")] = None,
    capabilities: Annotated[str | None, Header(alias="X-Capabilities")] = None,
) -> IdeaEvidencePackMaterializationResponse:
    caller_context = caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=booking_center_code,
        role=role,
        correlation_id=correlation_id,
        trace_id=trace_id,
    )
    capability_set = {item.strip() for item in (capabilities or "").split(",") if item.strip()}
    if (
        caller_context.caller_application != "lotus-idea"
        or "report.idea-materialization.recover" not in capability_set
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "idea_materialization_recovery_forbidden",
                "message": "The caller is not authorized to recover Idea materializations.",
            },
        )
    expected_identity = IdeaEvidenceMaterializationRecoveryIdentity(
        report_evidence_pack_id=report_evidence_pack_id,
        conversion_intent_id=conversion_intent_id,
        candidate_id=candidate_id,
        evidence_packet_id=evidence_packet_id,
        evidence_content_fingerprint=evidence_content_fingerprint,
        portfolio_id=portfolio_id,
    )
    try:
        return recover_idea_materialization(
            ledger=ledger,
            tenant_id=caller_context.tenant_id,
            idempotency_key=idempotency_key,
            expected_identity=expected_identity,
        )
    except IdeaMaterializationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "idea_materialization_not_found",
                "message": "No Idea materialization exists in the caller's tenant scope.",
            },
        ) from exc
    except IdeaMaterializationIdentityConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idea_materialization_identity_conflict",
                "message": "The stored Idea materialization identity is inconsistent.",
            },
        ) from exc


def _materialization_response(
    *,
    ledger: ReportJobLedger,
    record: ReportJobLedgerRecord,
    request: IdeaEvidencePackMaterializationRequest,
    idempotency_key: str,
) -> IdeaEvidencePackMaterializationResponse:
    # Re-read through the same exact owner projection used by recovery so the
    # command response and later GET expose one version authority. The bounded
    # SQL read binds the job row and its append-only event count in one snapshot.
    return recover_idea_materialization(
        ledger=ledger,
        tenant_id=record.tenant_id,
        idempotency_key=idempotency_key,
        expected_identity=recovery_identity_from_request(request),
    )
