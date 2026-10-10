"""Controlled literal full-inception/calendar/trailing/history source contract."""

import calendar
from copy import deepcopy
from uuid import UUID

from app.composite_reporting.admission import response_digest
from app.composite_reporting.eligibility_admission import source_hash
from tests.unit.composite_reporting.fixtures import selection_for
from tests.unit.composite_reporting.supplied_history_examples import (
    decision,
    supplied_history_example,
)


def cohesive_example(corrected=False):
    _, template, definition, _, old_captures = supplied_history_example(True, True)
    original = deepcopy(old_captures[0].source_response)
    original["decisions"] = [decision("A"), decision("B")]
    original["content_hash"] = source_hash(original)
    correction = deepcopy(old_captures[1].source_response)
    correction.update(
        affected_from="2026-01-01",
        affected_to="2026-01-31",
        decisions=[
            decision("A", last="2026-01-31", excluded=True),
            decision("A", first="2026-02-01"),
            decision("B"),
        ],
    )
    correction["content_hash"] = source_hash(correction)
    captures = []
    for capture, raw in zip(old_captures, [original, correction], strict=True):
        pin = capture.pin.model_copy(
            update={"content_hash": raw["content_hash"], "response_digest": response_digest(raw)}
        )
        captures.append(
            capture.model_copy(
                update={
                    "pin": pin,
                    "source_response": raw,
                    "source_response_digest": pin.response_digest,
                }
            )
        )
    captures = captures if corrected else captures[:1]
    source = deepcopy(template)
    periods, windows = [], []
    for month in range(1, 13):
        first, last = (
            f"2026-{month:02d}-01",
            f"2026-{month:02d}-{calendar.monthrange(2026, month)[1]}",
        )
        correction_month = corrected and month == 1
        window = deepcopy(template["selection_manifest"]["windows"][0])
        window.update(
            materialization_id=str(UUID(int=month + (100 if correction_month else 0))),
            period_start=first,
            period_end=last,
            membership_content_hash=correction["content_hash"]
            if correction_month
            else original["content_hash"],
            restatement_sequence=2 if correction_month else 1,
            source_cut_id="synthetic-cohesion-corrected"
            if correction_month
            else "synthetic-cohesion-original",
            retained_receipt_fingerprint="sha256:" + ("e" if correction_month else "d") * 64,
        )
        period = deepcopy(template["periods"][0])
        members = ["B"] if correction_month else ["A", "B"]
        period.update(
            period_start=first,
            period_end=last,
            return_value=("0.020000000000" if corrected else "0.010000000000")
            if month == 1
            else "0.000000000000",
            cumulative_return="0.020000000000" if corrected else "0.010000000000",
            member_count=len(members),
            excluded_member_count=1 if correction_month else 0,
            restatement_sequence=window["restatement_sequence"],
            beginning_market_value="100" if correction_month else "200",
            ending_market_value=("102" if correction_month else "202") if month == 1 else "200",
        )
        period["member_contributions"] = [
            deepcopy(template["periods"][0]["member_contributions"][0])
            | {
                "portfolio_id": member,
                "period_start": first,
                "period_end": last,
                "restatement_sequence": window["restatement_sequence"],
                "return_value": "0.020000000000"
                if member == "B" and month == 1
                else "0.000000000000",
                "contribution": ("0.020000000000" if correction_month else "0.010000000000")
                if member == "B" and month == 1
                else "0.000000000000",
                "beginning_market_value": "100",
                "beginning_asset_weight": "1" if correction_month else "0.5",
            }
            for member in members
        ]
        periods.append(period)
        windows.append(window)
    source.update(
        period_start=windows[0]["period_start"],
        period_end=windows[-1]["period_end"],
        periods=periods,
        cumulative_return=periods[-1]["cumulative_return"],
    )
    source["selection_manifest"]["windows"] = windows
    sources = []
    for index, offset in enumerate([0, 0, 10]):
        raw = deepcopy(source)
        raw["calculation_id"] = str(UUID(int=200 + index + (10 if corrected and index < 2 else 0)))
        raw["selection_manifest"]["calculation_fingerprint"] = (
            "sha256:" + ("abc"[index] if not corrected or index == 2 else "ef"[index]) * 64
        )
        raw["selection_manifest"]["windows"] = windows[offset:]
        raw["periods"] = deepcopy(periods[offset:])
        raw["period_start"] = windows[offset]["period_start"]
        if offset:
            raw["cumulative_return"] = "0.000000000000"
            for period in raw["periods"]:
                period["cumulative_return"] = "0.000000000000"
        sources.append(raw)
    request = {
        "selection": selection_for(sources[0]).model_dump(mode="json"),
        "source_products": [
            {
                "kind": "CALENDAR_RETURN",
                "product_key": "calendar_2026",
                "year": 2026,
                "selection": selection_for(sources[1]).model_dump(mode="json"),
            },
            {
                "kind": "TRAILING_RETURN",
                "product_key": "trailing_2m",
                "months": 2,
                "selection": selection_for(sources[2]).model_dump(mode="json"),
            },
        ],
        "source_context": {
            "definition": definition.pin.model_dump(mode="json"),
            "since_inception": True,
            "memberships": [capture.pin.model_dump(mode="json") for capture in captures],
        },
        "requested_output_formats": ["xlsx"],
    }
    return request, {raw["calculation_id"]: raw for raw in sources}, definition, captures
