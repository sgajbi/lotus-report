from __future__ import annotations


def record_report_job_work_lease_event(*, outcome: str, count: int = 1) -> None:
    """Emit lease telemetry without coupling persistence module import order to metrics setup."""
    from app.reporting_metrics import record_report_job_work_lease_event as record_event

    record_event(outcome=outcome, count=count)
