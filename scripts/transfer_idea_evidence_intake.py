"""Host-side entry point for the intake ledger transfer.

A thin delegator. The implementation and its CLI live in
`app.idea_evidence_intake.transfer`, because the deployed image ships `src/`
and not `scripts/` -- and the ledger being transferred is in a volume mounted
into that image, so the operator runs it there as
`python -m app.idea_evidence_intake.transfer`.

This exists so a developer with the repository checked out can run the same
code without arranging `PYTHONPATH` themselves. Two entry points, one
implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path = [path for path in sys.path if path != str(SRC)]
sys.path.insert(0, str(SRC))

from app.idea_evidence_intake.transfer import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
