"""Synthetic accepted-source example; no producer or official authority claim."""

from copy import deepcopy

from app.composite_reporting.admission import response_digest
from app.composite_reporting.models import CompositeReportSelection

CALCULATION_ID = "00000000-0000-0000-0000-000000000099"


def calculated_example():
    # These expected facts are literal OR-01/OR-02 oracles, not an implementation
    # of the financial calculation under test. Performance acceptance is separate.
    windows = [
        {
            "materialization_id": "00000000-0000-0000-0000-00000000000" + str(index),
            "period_start": start,
            "period_end": end,
            "restatement_sequence": 1,
            "definition_content_hash": "sha256:" + "a" * 64,
            "membership_content_hash": "sha256:" + "b" * 64,
            "attestation_content_hash": "sha256:" + "c" * 64,
            "source_cut_id": "source-cut-" + str(index),
            "method_binding": {"method_id": "ASSET_WEIGHTED", "method_version": "v1"},
            "retained_receipt_fingerprint": "sha256:" + str(index) * 64,
        }
        for index, start, end in (
            (1, "2026-01-01", "2026-01-31"),
            (2, "2026-02-01", "2026-02-28"),
        )
    ]
    periods = []
    for index, window in enumerate(windows):
        members = []
        for member_id, assets, weight, returns, contributions in (
            ("00000000000000000001", "100", "0.25", ("0.10", "0.02"), ("0.025", "0.005")),
            ("=literal-identifier", "300", "0.75", ("-0.02", "0.02"), ("-0.015", "0.015")),
        ):
            members.append(
                {
                    "portfolio_id": member_id,
                    "period_start": window["period_start"],
                    "period_end": window["period_end"],
                    "return_value": returns[index],
                    "beginning_market_value": assets,
                    "beginning_asset_weight": weight,
                    "contribution": contributions[index],
                    "source_snapshot_id": "member-snapshot-" + member_id,
                    "source_fingerprint": "member-fingerprint-" + member_id,
                    "restatement_version": "original-v1",
                    "restatement_sequence": 1,
                    "calculation_id": "member-calculation-" + member_id,
                    "source_authority_identity": None,
                }
            )
        periods.append(
            {
                "period_start": window["period_start"],
                "period_end": window["period_end"],
                "status": "READY",
                "return_value": ("0.010000000000", "0.020000000000")[index],
                "cumulative_return": ("0.010000000000", "0.030200000000")[index],
                "beginning_market_value": "400",
                "ending_market_value": ("404", "408")[index],
                "member_count": 2,
                "excluded_member_count": 0,
                "dispersion_equal_weight": None,
                "return_view": "NET_ACTUAL",
                "reporting_currency": "USD",
                "source_fingerprints": [item["source_fingerprint"] for item in members],
                "restatement_versions": ["original-v1"],
                "restatement_sequence": 1,
                "reason_codes": [],
                "member_contributions": members,
            }
        )
    return {
        "calculation_id": CALCULATION_ID,
        "composite_id": "NEUTRAL_BALANCED",
        "status": "READY",
        "period_start": "2026-01-01",
        "period_end": "2026-02-28",
        "cumulative_return": "0.030200000000",
        "reason_codes": [],
        "periods": periods,
        "methodology": "persisted_member_return_asset_weighted_twr_v1",
        "selection_manifest": {
            "qualification": "EXPLICIT_RETAINED_CALCULATED_REPLAY",
            "windows": windows,
            "engine_version": "synthetic-engine.v1",
            "calculation_fingerprint": "sha256:" + "d" * 64,
        },
    }


def selection_for(payload):
    return CompositeReportSelection.model_validate(
        {
            "tenant_id": "tenant-a",
            "composite_id": payload["composite_id"],
            "calculation_id": payload["calculation_id"],
            "period_start": payload["period_start"],
            "period_end": payload["period_end"],
            "reporting_currency": "USD",
            "return_view": "NET_ACTUAL",
            "methodology": payload["methodology"],
            "engine_version": payload["selection_manifest"]["engine_version"],
            "calculation_fingerprint": payload["selection_manifest"]["calculation_fingerprint"],
            "response_digest": response_digest(payload),
            "windows": deepcopy(payload["selection_manifest"]["windows"]),
        }
    )
