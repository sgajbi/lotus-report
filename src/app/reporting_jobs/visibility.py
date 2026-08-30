"""Job-identity visibility fence (issue #203).

Tenant and region are segregation boundaries, and a booking centre stamped on
a job binds it to that booking centre: a caller from another tenant, region,
or booking centre must never read or command another tenant's report evidence
by guessing or leaking an opaque job id. Mismatches answer exactly like an
unknown id, so cross-tenant existence is never leaked.

Report sits behind Gateway's entitlement; this fence is the report-side
defense-in-depth that makes the trusted caller headers actually bind.
"""

from app.reporting_jobs.ledger import ReportJobNotFoundError
from app.reporting_jobs.models import ReportCallerContext, ReportJobLedgerRecord


def assert_job_visible(
    job: ReportJobLedgerRecord,
    caller_context: ReportCallerContext,
) -> None:
    """Raise the unknown-id not-found error unless the caller's segregation
    identity matches the job's. A job without a stored booking centre accepts
    any caller booking centre; a job stamped with one requires the same value
    (omission must not bypass the fence)."""

    if (
        job.tenant_id != caller_context.tenant_id
        or job.region != caller_context.region
        or (
            job.booking_center_code is not None
            and caller_context.booking_center_code != job.booking_center_code
        )
    ):
        raise ReportJobNotFoundError("report_job_not_found")
