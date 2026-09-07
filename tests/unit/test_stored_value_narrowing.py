"""The guards that narrow stored text to the vocabularies models declare.

Each of these replaced a `str(...)` call that mypy could not see was wrong,
because `follow_imports = skip` resolved every import to `Any`. They are worth
testing for the reason any guard is: a check that cannot refuse is not a check,
and the value of these is entirely in what they refuse.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from app.report_batch_orchestrator.postgres_ledger import _count_from
from app.reporting_jobs.models import (
    OutcomeReviewReportJobRequest,
    _first_example,
)
from app.reporting_lineage.models import (
    SNAPSHOT_POSTURES,
    UPSTREAM_FAILURE_CATEGORIES,
)
from app.reporting_lineage.store import POSTURE_VALUES, _required_dt_from_text
from app.typing_support import literal_value


class TestLiteralValue:
    def test_a_declared_member_passes_through(self) -> None:
        narrowed = literal_value("partial", SNAPSHOT_POSTURES, field="supportability_status")

        assert narrowed == "partial"

    def test_a_value_outside_the_vocabulary_is_refused(self) -> None:
        with pytest.raises(ValueError) as exc:
            literal_value("nearly_complete", SNAPSHOT_POSTURES, field="supportability_status")

        # The field and the offending value both appear: a stored row that has
        # drifted is only actionable if the message says which column and what
        # it held.
        assert "supportability_status" in str(exc.value)
        assert "nearly_complete" in str(exc.value)

    def test_a_member_of_a_different_vocabulary_is_refused(self) -> None:
        """The two lineage vocabularies overlap on `redacted` and nothing else.

        A posture is not a failure category. Passing the wrong tuple is the
        realistic mistake here, so it must not silently succeed.
        """

        with pytest.raises(ValueError):
            literal_value("timeout", SNAPSHOT_POSTURES, field="supportability_status")
        with pytest.raises(ValueError):
            literal_value("complete", UPSTREAM_FAILURE_CATEGORIES, field="failure_category")


class TestPostureValuesFeedTheCheckConstraint:
    def test_the_sql_constraint_uses_the_alias_members(self) -> None:
        """The database CHECK and the model must permit the same set.

        `POSTURE_VALUES` is interpolated into `CHECK (supportability_status IN
        ...)`. It used to be a second hand-written copy of the same six values,
        so a posture added to `SnapshotPosture` would have been accepted by the
        model and rejected by the column.
        """

        assert POSTURE_VALUES == SNAPSHOT_POSTURES


class TestCountFrom:
    def test_it_reads_the_aggregate_count(self) -> None:
        assert _count_from({"count": 7}) == 7

    def test_a_missing_row_is_refused_by_name(self) -> None:
        with pytest.raises(ValueError, match="aggregate_count_query_returned_no_row"):
            _count_from(None)


class TestRequiredDtFromText:
    def test_it_parses_and_normalises_to_utc(self) -> None:
        parsed = _required_dt_from_text("2026-09-07T10:30:00Z", field="captured_at")

        assert parsed == datetime(2026, 9, 7, 10, 30, tzinfo=UTC)

    def test_a_null_in_a_not_null_column_is_refused_by_name(self) -> None:
        with pytest.raises(ValueError) as exc:
            _required_dt_from_text(None, field="captured_at")

        assert "captured_at" in str(exc.value)


class TestFirstExample:
    def test_it_returns_the_declared_example(self) -> None:
        declared = OutcomeReviewReportJobRequest.model_fields["outcome_report_input"].examples
        assert declared is not None

        assert _first_example(OutcomeReviewReportJobRequest, "outcome_report_input") == declared[0]

    def test_a_field_with_no_examples_is_refused_by_name(self) -> None:
        """These constants are built at import time.

        Indexing `examples` directly meant a field that lost its `examples=[...]`
        failed application startup with `'NoneType' object is not subscriptable`,
        naming neither the model nor the field.
        """

        class _NoExamples(BaseModel):
            declared_without_examples: str

        with pytest.raises(ValueError) as exc:
            _first_example(_NoExamples, "declared_without_examples")

        assert "declared_without_examples" in str(exc.value)
        assert "_NoExamples" in str(exc.value)
