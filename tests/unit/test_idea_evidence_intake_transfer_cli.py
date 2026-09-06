"""The operator's whole interface to the transfer.

Argument handling and exit codes, with the transfer itself stubbed: whether the
records move is proven against real PostgreSQL elsewhere, and what matters here
is that a failure exits non-zero so it can gate a cutover, and that a missing
database URL is refused rather than treated as an empty transfer.
"""

from __future__ import annotations

import pytest

from app.idea_evidence_intake import transfer as transfer_module
from app.idea_evidence_intake.transfer import IntakeTransferError, TransferReport, main


def test_a_missing_database_url_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REPORT_JOB_LEDGER_DATABASE_URL", raising=False)

    assert main(["--sqlite-path", "irrelevant.sqlite3"]) == 1


def test_a_failed_transfer_exits_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """So it can gate the cutover directly, rather than the operator reading output."""
    monkeypatch.setenv("REPORT_JOB_LEDGER_DATABASE_URL", "postgresql://stub/stub")

    def _fail(**_kwargs: object) -> TransferReport:
        raise IntakeTransferError("intake transfer incomplete: source=3 verified=2")

    monkeypatch.setattr(transfer_module, "transfer_intake_ledger", _fail)

    assert main(["--sqlite-path", "irrelevant.sqlite3"]) == 1


def test_a_complete_transfer_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPORT_JOB_LEDGER_DATABASE_URL", "postgresql://stub/stub")

    def _succeed(**_kwargs: object) -> TransferReport:
        return TransferReport(source_records=3, inserted=3, verified=3)

    monkeypatch.setattr(transfer_module, "transfer_intake_ledger", _succeed)

    assert main(["--sqlite-path", "irrelevant.sqlite3"]) == 0


def test_the_default_path_follows_the_configured_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator who omits --sqlite-path must not silently transfer nothing."""
    monkeypatch.setenv("REPORT_JOB_LEDGER_DATABASE_URL", "postgresql://stub/stub")
    monkeypatch.setenv("IDEA_EVIDENCE_INTAKE_LEDGER_PATH", "/app/data/configured.sqlite3")
    seen: dict[str, object] = {}

    def _capture(**kwargs: object) -> TransferReport:
        seen.update(kwargs)
        return TransferReport(source_records=0, verified=0)

    monkeypatch.setattr(transfer_module, "transfer_intake_ledger", _capture)

    assert main([]) == 0
    assert seen["sqlite_path"] == "/app/data/configured.sqlite3"
