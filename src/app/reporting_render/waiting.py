"""The explicit owner-side waiting outcome (report#303).

A leaf module: consumed by the render service (producer), the execution
service, and the recovery operator services without import cycles.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.reporting_jobs.models import ReportJobLedgerRecord


@dataclass(frozen=True)
class RenderWaiting:
    """Waiting is not failure and not completion: the job stays NONTERMINAL
    in whatever recovery status it holds (rendering, completed, archiving)
    and the work queue DEFERS without burning the failure budget - a
    completed job whose custody outcome is still unresolved must keep
    polling, never be terminally completed on a transient owner outage."""

    job: ReportJobLedgerRecord
