"""Which store backs the intake ledger is a configured decision, not a guess.

report#326 introduces a second backend. The selector is three lines, and it is
exactly the kind of code that goes untested and then silently resolves the
wrong way in one environment -- which for this store means starting from an
empty intake ledger, the unverifiable-replay state report#334 refuses.

The default is asserted as well as the override: a selector that always
returned PostgreSQL would satisfy an override-only test while changing every
deployment.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.idea_evidence_intake.postgres_ledger import PostgresIdeaEvidenceIntakeLedger
from app.idea_evidence_intake.service import IdeaEvidenceIntakeLedger
from app.routers.idea_evidence_intake import get_idea_evidence_intake_ledger


@pytest.fixture(autouse=True)
def _clear_ledger_cache():
    """The dependency is lru_cached, so a stale entry would answer for the next test."""
    get_idea_evidence_intake_ledger.cache_clear()
    yield
    get_idea_evidence_intake_ledger.cache_clear()


def test_the_default_backend_is_sqlite() -> None:
    """Deliberate, and load-bearing.

    Nothing has transferred existing intake records into PostgreSQL yet
    (report#326 slice 3), so defaulting to it would start a deployment from an
    empty ledger -- report rows surviving while the evidence that validated
    them does not.
    """
    assert settings.idea_evidence_intake_ledger_backend == "sqlite"
    assert isinstance(get_idea_evidence_intake_ledger(), IdeaEvidenceIntakeLedger)


def test_postgresql_is_selected_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selection only -- the provider factory is stubbed on purpose.

    PostgresConnectionProvider opens its minimum connections in __init__, so
    building a real one here would make a unit test require a database. It
    would pass on a developer machine with one running and fail in the unit
    lane, which is the worst way for a test to be wrong.
    """
    monkeypatch.setattr(settings, "idea_evidence_intake_ledger_backend", "postgresql")
    monkeypatch.setattr(
        "app.routers.idea_evidence_intake.get_postgres_connection_provider",
        lambda: _NeverUsedConnectionProvider(),
    )

    ledger = get_idea_evidence_intake_ledger()

    assert isinstance(ledger, PostgresIdeaEvidenceIntakeLedger)


class _NeverUsedConnectionProvider:
    """Stands in for a pool without opening one.

    Fails loudly rather than silently if selection ever tries to use it: this
    test is about which ledger is built, and a stub that quietly answered
    queries would let a connection bug pass as a selection success.
    """

    def connection(self):
        raise AssertionError("backend selection must not open a connection")
