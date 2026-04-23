from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path = [path for path in sys.path if path != str(SRC)]
sys.path.insert(0, str(SRC))

from app.reporting_jobs.ledger import ReportJobLedger  # noqa: E402

REQUIRED_DOC = Path("docs/standards/migration-contract.md")
REQUIRED_PHRASES = (
    "report job ledger schema",
    "forward-fix",
    "forward-only schema",
    "report_request",
    "report_job",
    "report_status_event",
)


def run_ledger_schema_checks() -> int:
    if not REQUIRED_DOC.exists():
        print(f"Missing required migration contract document: {REQUIRED_DOC}")
        return 1

    content = REQUIRED_DOC.read_text(encoding="utf-8").lower()
    missing = [phrase for phrase in REQUIRED_PHRASES if phrase not in content]
    if missing:
        print("Migration contract document is missing required phrases:")
        for phrase in missing:
            print(f"- {phrase}")
        return 1

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "report-job-ledger.sqlite3"
        ReportJobLedger(db_path)
        if not db_path.exists():
            print("Ledger schema smoke failed: database was not created.")
            return 1

    print("Migration contract check passed (report job ledger schema mode).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate migration contract requirements.")
    parser.add_argument("--mode", choices=["ledger-schema", "no-schema"], default="ledger-schema")
    args = parser.parse_args()

    if args.mode in {"ledger-schema", "no-schema"}:
        return run_ledger_schema_checks()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
