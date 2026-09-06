"""Carry the SQLite intake ledger into PostgreSQL, then prove it arrived.

    REPORT_JOB_LEDGER_DATABASE_URL=... \\
      python scripts/transfer_idea_evidence_intake.py \\
        --sqlite-path data/idea-evidence-intake.sqlite3

Safe to re-run: a completed transfer reports every record as already present
and verifies them again, which is how an operator confirms a cutover without
changing anything. An interrupted run leaves a prefix that a re-run completes.

Exits non-zero if any record is missing or differs, so it can gate a cutover.
It does NOT switch the backend -- that stays a separate, deliberate step, and
the runbook sequences it after this reports a complete transfer.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path = [path for path in sys.path if path != str(SRC)]
sys.path.insert(0, str(SRC))

from app.idea_evidence_intake.transfer import (  # noqa: E402
    IntakeTransferError,
    transfer_intake_ledger,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        default=os.environ.get(
            "IDEA_EVIDENCE_INTAKE_LEDGER_PATH", "data/idea-evidence-intake.sqlite3"
        ),
        help="The SQLite intake ledger to read. Defaults to the configured path.",
    )
    args = parser.parse_args()

    database_url = os.environ.get("REPORT_JOB_LEDGER_DATABASE_URL")
    if not database_url:
        print("REPORT_JOB_LEDGER_DATABASE_URL is required to transfer the intake ledger.")
        return 1

    try:
        report = transfer_intake_ledger(sqlite_path=args.sqlite_path, database_url=database_url)
    except IntakeTransferError as exc:
        print(f"Intake ledger transfer FAILED: {exc}")
        return 1

    print(f"Intake ledger transfer complete: {report.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
