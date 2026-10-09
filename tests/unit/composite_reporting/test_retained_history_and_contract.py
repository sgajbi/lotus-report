"""Exact shared artifacts and history longer than the former five-year cutoff."""

import calendar
import json
from copy import deepcopy
from datetime import date
from pathlib import Path
from uuid import UUID

from app.composite_reporting.semantic_contract import CompositeReportData, composite_report_schema
from tests.unit.composite_reporting.fixtures import calculated_example
from tests.unit.composite_reporting.test_table_contract import example_tables


def test_shared_schema_and_example_match_the_executable_producer():
    root = Path(__file__).resolve().parents[3]
    schema = json.loads((root / "contracts/composite_review.v1.schema.json").read_text())
    example = json.loads((root / "contracts/examples/composite-review.v1.json").read_text())
    assert schema == composite_report_schema()
    assert example == example_tables()
    validated = CompositeReportData.model_validate(example)
    assert validated.source_response == example["source_response"]


def test_seventy_two_months_retain_every_period_member_and_window():
    payload = calculated_example()
    period_template = payload["periods"][0]
    window_template = payload["selection_manifest"]["windows"][0]
    periods, windows = [], []
    for index in range(72):
        year, month = 2020 + index // 12, 1 + index % 12
        start = date(year, month, 1).isoformat()
        end = date(year, month, calendar.monthrange(year, month)[1]).isoformat()
        window = deepcopy(window_template)
        window.update(
            materialization_id=str(UUID(int=index + 1)), period_start=start, period_end=end
        )
        period = deepcopy(period_template)
        period.update(period_start=start, period_end=end, return_value="0", cumulative_return="0")
        for member in period["member_contributions"]:
            member.update(period_start=start, period_end=end, return_value="0", contribution="0")
        periods.append(period)
        windows.append(window)
    payload.update(
        periods=periods, period_start="2020-01-01", period_end="2025-12-31", cumulative_return="0"
    )
    payload["selection_manifest"]["windows"] = windows
    data = example_tables(payload)
    CompositeReportData.model_validate(data)
    monthly = next(table for table in data["tables"] if table["table_id"] == "MonthlyReturns")
    contributions = next(table for table in data["tables"] if table["table_id"] == "Contribution")
    assert len(monthly["rows"]) == 72
    assert len(contributions["rows"]) == 144
    assert data["source_response"] == payload
    assert data["selection"]["windows"] == windows
    assert monthly["rows"][0]["cells"]["period_start"]["canonical_value"] == "2020-01-01"
    assert monthly["rows"][-1]["cells"]["period_end"]["canonical_value"] == "2025-12-31"
