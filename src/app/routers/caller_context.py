from typing import Annotated

from fastapi import Header, HTTPException, status

from app.observability import CORRELATION_ID_HEADER_ALIAS, TRACE_ID_HEADER_ALIAS
from app.reporting_jobs.models import ReportCallerContext


def caller_context_from_headers(
    *,
    triggered_by: str | None,
    caller_application: str | None,
    tenant_id: str | None,
    region: str | None,
    booking_center_code: str | None,
    role: str | None,
    correlation_id: str | None,
    trace_id: str | None,
) -> ReportCallerContext:
    missing = [
        name
        for name, value in {
            "X-Actor-Id": triggered_by,
            "X-Caller-Application": caller_application,
            "X-Tenant-Id": tenant_id,
            "X-Region": region,
        }.items()
        if not value or not value.strip()
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "missing_caller_context",
                "message": "Required caller context headers are missing.",
                "missing_headers": missing,
            },
        )
    assert triggered_by is not None
    assert caller_application is not None
    assert tenant_id is not None
    assert region is not None
    return ReportCallerContext(
        triggered_by=triggered_by.strip(),
        caller_application=caller_application.strip(),
        tenant_id=tenant_id.strip(),
        region=region.strip(),
        booking_center_code=booking_center_code,
        role=role,
        correlation_id=correlation_id or "",
        trace_id=trace_id or "",
    )


def caller_context_dependency(
    actor_id: Annotated[
        str | None,
        Header(alias="X-Actor-Id", description="Authenticated actor or system principal."),
    ] = None,
    caller_application: Annotated[
        str | None,
        Header(alias="X-Caller-Application", description="Calling Lotus application."),
    ] = None,
    tenant_id: Annotated[
        str | None,
        Header(alias="X-Tenant-Id", description="Tenant identifier for entitlement and audit."),
    ] = None,
    region: Annotated[
        str | None,
        Header(alias="X-Region", description="Operating region for segregation and audit."),
    ] = None,
    booking_center_code: Annotated[
        str | None,
        Header(alias="X-Booking-Center-Code", description="Optional booking center code."),
    ] = None,
    role: Annotated[
        str | None,
        Header(alias="X-Role", description="Optional caller role for audit diagnostics."),
    ] = None,
    correlation_id: Annotated[
        str | None,
        Header(alias=CORRELATION_ID_HEADER_ALIAS, description="End-to-end correlation identifier."),
    ] = None,
    trace_id: Annotated[
        str | None,
        Header(alias=TRACE_ID_HEADER_ALIAS, description="Distributed trace identifier."),
    ] = None,
) -> ReportCallerContext:
    return caller_context_from_headers(
        triggered_by=actor_id,
        caller_application=caller_application,
        tenant_id=tenant_id,
        region=region,
        booking_center_code=booking_center_code,
        role=role,
        correlation_id=correlation_id,
        trace_id=trace_id,
    )
