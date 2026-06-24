from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.idea_evidence_intake.models import (
    IdeaEvidencePackIntakeRequest,
    IdeaEvidencePackIntakeResponse,
)
from app.idea_evidence_intake.service import (
    IdeaEvidenceIntakeConflictError,
    IdeaEvidenceIntakeLedger,
)
from app.routers.caller_context import caller_context_from_headers

router = APIRouter(prefix="/reports/idea-evidence-packs", tags=["Report Evidence"])

_IDEA_EVIDENCE_INTAKE_LEDGER = IdeaEvidenceIntakeLedger()


def get_idea_evidence_intake_ledger() -> IdeaEvidenceIntakeLedger:
    return _IDEA_EVIDENCE_INTAKE_LEDGER


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
    caller_context_from_headers(
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
    try:
        return ledger.accept(
            request,
            idempotency_key=idempotency_key.strip(),
            correlation_id=correlation_id,
        )
    except IdeaEvidenceIntakeConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idea_evidence_intake_conflict",
                "message": "Idea evidence intake idempotency key was replayed with new content.",
            },
        ) from exc
