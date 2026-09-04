from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Awaitable, Callable, Protocol
from uuid import uuid4

from app.config import Settings, settings
from app.observability import correlation_id_var, request_id_var, setup_logging, trace_id_var
from app.reporting_jobs.service import get_report_job_worker
from app.reporting_jobs.work_queue import ReportJobWorkRetryPolicy
from app.reporting_jobs.worker import ReportJobWorkerRunResult
from app.reporting_metrics import (
    record_report_job_worker_metrics,
    validate_reporting_metric_contracts,
)

Sleep = Callable[[float], Awaitable[None]]


class ReportJobRuntimeWorker(Protocol):
    async def run_once(
        self,
        *,
        worker_id: str,
        max_items: int,
        lease_seconds: int,
    ) -> ReportJobWorkerRunResult: ...


@dataclass(frozen=True)
class ReportJobWorkerProcessConfig:
    worker_id: str
    interval_seconds: float
    max_items_per_pass: int
    lease_seconds: int
    retry_policy: ReportJobWorkRetryPolicy


def report_job_worker_config_from_settings(
    source: Settings = settings,
) -> ReportJobWorkerProcessConfig:
    return ReportJobWorkerProcessConfig(
        worker_id=source.report_job_worker_id,
        interval_seconds=source.report_job_worker_interval_seconds,
        max_items_per_pass=source.report_job_worker_max_items_per_pass,
        lease_seconds=source.report_job_worker_lease_seconds,
        retry_policy=ReportJobWorkRetryPolicy(
            max_attempts=source.report_job_worker_max_attempts,
            base_delay_seconds=source.report_job_worker_retry_base_seconds,
            max_delay_seconds=source.report_job_worker_retry_max_seconds,
        ),
    )


class ReportJobWorkerProcess:
    def __init__(
        self,
        *,
        worker: ReportJobRuntimeWorker,
        config: ReportJobWorkerProcessConfig,
        sleep: Sleep = asyncio.sleep,
        logger: logging.Logger | None = None,
        maintenance: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._worker = worker
        self._config = config
        self._sleep = sleep
        self._logger = logger or logging.getLogger("report_job_worker")
        self._maintenance = maintenance
        self._stopping = False

    def stop(self) -> None:
        self._stopping = True

    async def run(self, *, max_iterations: int | None = None) -> None:
        iteration = 0
        while not self._stopping:
            iteration += 1
            suffix = uuid4().hex
            correlation_id = f"corr-report-job-worker-{iteration}-{suffix[:12]}"
            corr_token = correlation_id_var.set(correlation_id)
            req_token = request_id_var.set(f"report_job_worker_pass_{iteration}")
            trace_token = trace_id_var.set(suffix)
            started_at = perf_counter()
            try:
                result = await self._worker.run_once(
                    worker_id=self._config.worker_id,
                    max_items=self._config.max_items_per_pass,
                    lease_seconds=self._config.lease_seconds,
                )
                if self._maintenance is not None:
                    # Bounded convergence work that must not depend on new
                    # client commands - e.g. re-attempting pending Archive
                    # lineage pairs so a transient outage self-heals. A
                    # maintenance failure is logged, never fatal to the pass.
                    try:
                        await self._maintenance()
                    except Exception:
                        self._logger.exception("report_job_worker.maintenance_failed")
                record_report_job_worker_metrics(
                    claimed_count=result.claimed_count,
                    completed_count=result.completed_count,
                    retry_pending_count=result.retry_pending_count,
                    failed_count=result.failed_count,
                    duration_seconds=perf_counter() - started_at,
                )
                self._logger.info(
                    "report_job_worker.pass_completed",
                    extra={
                        "extra_fields": {
                            "worker_id": self._config.worker_id,
                            "iteration": iteration,
                            "claimed_count": result.claimed_count,
                            "completed_count": result.completed_count,
                            "retry_pending_count": result.retry_pending_count,
                            "failed_count": result.failed_count,
                        }
                    },
                )
            except Exception:
                record_report_job_worker_metrics(
                    claimed_count=0,
                    completed_count=0,
                    retry_pending_count=0,
                    failed_count=0,
                    status="failed",
                    failure_category="report_job_worker_runtime_error",
                    duration_seconds=perf_counter() - started_at,
                )
                raise
            finally:
                correlation_id_var.reset(corr_token)
                request_id_var.reset(req_token)
                trace_id_var.reset(trace_token)

            if max_iterations is not None and iteration >= max_iterations:
                break
            await self._sleep(self._config.interval_seconds)


async def run_report_job_worker_process(
    *,
    worker: ReportJobRuntimeWorker | None = None,
    source_settings: Settings = settings,
    max_iterations: int | None = None,
) -> None:
    config = report_job_worker_config_from_settings(source_settings)
    process = ReportJobWorkerProcess(
        worker=worker or get_report_job_worker(retry_policy=config.retry_policy),
        config=config,
        maintenance=build_archive_lineage_maintenance(source_settings),
    )
    await process.run(max_iterations=max_iterations)


def build_archive_lineage_maintenance(
    source_settings: Settings = settings,
) -> Callable[[], Awaitable[None]]:
    """One bounded reconciliation pass per worker iteration.

    Reuses the durable job events and the idempotent lineage recorder - no
    new store, no new workflow. Evaluation condition: a transient Archive
    lifecycle outage self-heals after recovery even when nobody orders
    another correction.
    """

    from app.clients.archive_client import ArchiveClient
    from app.reporting_jobs.service import get_report_job_ledger
    from app.reporting_metrics import record_archive_lineage_reconciliation
    from app.reporting_render.archive_lineage import reconcile_pending_archive_lineage

    archive_client = ArchiveClient(
        base_url=source_settings.archive_base_url,
        timeout_seconds=source_settings.upstream_timeout_seconds,
        max_retries=source_settings.upstream_max_retries,
        retry_backoff_seconds=source_settings.upstream_retry_backoff_seconds,
    )

    async def _maintenance() -> None:
        ledger = get_report_job_ledger()
        result = await reconcile_pending_archive_lineage(
            archive_client=archive_client,
            ledger=ledger,
            limit=source_settings.report_job_worker_lineage_reconciliation_limit,
        )
        record_archive_lineage_reconciliation(
            outstanding_jobs=int(result["outstanding_jobs"] or 0),
            oldest_age_seconds=(
                float(result["oldest_age_seconds"])
                if result["oldest_age_seconds"] is not None
                else None
            ),
        )

    return _maintenance


def start_worker_metrics_server(*, port: int | None = None) -> None:
    """Expose this worker process's Prometheus registry.

    The dedicated job worker runs the canonical asynchronous capture, render,
    archive, replay, and advisor-commentary paths, so their counters live in
    THIS process - without an exporter here they are invisible to scraping
    and every documented alert on them stays silently zero.
    """

    from prometheus_client import start_http_server

    validate_reporting_metric_contracts()
    start_http_server(port if port is not None else settings.report_job_worker_metrics_port)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the durable lotus-report job worker.")
    parser.add_argument("--once", action="store_true", help="Run one worker pass and exit.")
    parser.add_argument("--max-iterations", type=int, default=None)
    args = parser.parse_args()
    setup_logging()
    start_worker_metrics_server()
    max_iterations = 1 if args.once else args.max_iterations
    asyncio.run(run_report_job_worker_process(max_iterations=max_iterations))


if __name__ == "__main__":  # pragma: no cover
    main()
