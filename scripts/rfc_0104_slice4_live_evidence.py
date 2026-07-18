from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path = [path for path in sys.path if path != str(SRC)]
sys.path.insert(0, str(SRC))

from app.report_batch_orchestrator.dispatch import ReportBatchDispatcher  # noqa: E402
from app.report_batch_orchestrator.models import (  # noqa: E402
    BatchCreateRequest,
    BatchDispatchPolicy,
    PortfolioBatchCandidate,
)
from app.report_batch_orchestrator.postgres_ledger import PostgresReportBatchLedger  # noqa: E402
from app.reporting_jobs.models import ReportCallerContext  # noqa: E402
from app.reporting_jobs.postgres_ledger import PostgresReportJobLedger  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Produce RFC-0104 Slice 4 live PostgreSQL dispatch evidence."
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL"),
        help="PostgreSQL ledger URL. Defaults to REPORT_JOB_LEDGER_DATABASE_URL.",
    )
    parser.add_argument(
        "--output-directory",
        default=str(ROOT / "output"),
        help="Directory where evidence artifacts will be written.",
    )
    args = parser.parse_args()

    if not args.database_url:
        print("REPORT_JOB_LEDGER_DATABASE_URL or --database-url is required.", file=sys.stderr)
        return 2

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    evidence_dir = Path(args.output_directory) / f"rfc-0104-slice4-live-evidence-{timestamp}"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    suffix = uuid4().hex
    portfolio_ids = [
        f"PB_SG_GLOBAL_BAL_001_{suffix}",
        f"PB_SG_GLOBAL_BAL_002_{suffix}",
    ]
    caller_context = ReportCallerContext(
        triggered_by="advisor-123",
        caller_application="lotus-gateway",
        tenant_id="tenant-sg",
        region="APAC",
        booking_center_code="SG",
        role="advisor",
        correlation_id=f"corr-rfc-0104-slice4-{suffix}",
        trace_id=f"trace-rfc-0104-slice4-{suffix}",
    )
    batch_request = BatchCreateRequest(
        selector_mode="explicit_portfolio_list",
        portfolio_ids=portfolio_ids,
        source_candidates=[
            PortfolioBatchCandidate(
                portfolio_id=portfolio_id,
                tenant_id="tenant-sg",
                region="APAC",
                active=True,
            )
            for portfolio_id in portfolio_ids
        ],
        as_of_date="2026-04-22",
        requested_output_formats=["pdf", "json"],
        reporting_currency="USD",
        options={
            "sections": ["OVERVIEW", "PERFORMANCE"],
            "evidence_scope": "rfc-0104-slice-4",
        },
    )

    with (
        PostgresReportBatchLedger(args.database_url) as batch_ledger,
        PostgresReportJobLedger(args.database_url) as report_job_ledger,
    ):
        batch_ledger.check_ready()
        report_job_ledger.check_ready()

        batch = batch_ledger.create_batch(
            request=batch_request,
            caller_context=caller_context,
            idempotency_key=f"rfc-0104-slice4-batch-{suffix}",
        )
        dispatcher = ReportBatchDispatcher(
            batch_ledger=batch_ledger,
            report_job_ledger=report_job_ledger,
            policy=BatchDispatchPolicy(
                max_active_batches=1000,
                max_active_items=1000,
                max_active_upstream_jobs=10,
                max_active_render_jobs=10,
                max_active_archive_jobs=10,
            ),
        )
        dispatch_result = dispatcher.dispatch_batch(
            batch_id=batch.batch_id,
            caller_context=caller_context,
            worker_id=f"rfc-0104-slice4-worker-{suffix}",
        )
        refreshed_batch = batch_ledger.get_batch(batch.batch_id)
        report_jobs = [
            report_job_ledger.get_job(job_id).model_dump(mode="json")
            for job_id in dispatch_result.report_job_ids
        ]

    evidence = {
        "rfc": "RFC-0104",
        "slice": "Slice 4 - dispatch and concurrency primitives",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "database_url_shape": args.database_url.split("@")[-1],
        "batch_id": batch.batch_id,
        "batch_status": refreshed_batch.status,
        "portfolio_ids": portfolio_ids,
        "dispatch_result": dispatch_result.model_dump(mode="json"),
        "items": [item.model_dump(mode="json") for item in refreshed_batch.items],
        "report_jobs": report_jobs,
        "proof": {
            "postgres_schema_ready": True,
            "one_report_job_per_batch_item": len(report_jobs) == len(refreshed_batch.items),
            "all_items_waiting_on_report_job": all(
                item.status == "waiting_on_report_job" and item.report_job_id
                for item in refreshed_batch.items
            ),
            "idempotency_keys_match_batch_items": sorted(
                job["idempotency_key"] for job in report_jobs
            )
            == sorted(item.item_idempotency_key for item in refreshed_batch.items),
        },
    }

    evidence_path = evidence_dir / "dispatch-evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    if not all(evidence["proof"].values()):
        print(f"RFC-0104 Slice 4 evidence failed: {evidence_path}", file=sys.stderr)
        return 1

    print(f"RFC-0104 Slice 4 evidence written: {evidence_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
