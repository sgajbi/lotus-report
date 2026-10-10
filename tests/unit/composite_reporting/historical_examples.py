"""Select complete, immutable Manage-controlled graphs without resealing products."""

import calendar
import json
from pathlib import Path

from app.composite_reporting.admission import response_digest
from app.composite_reporting.models import HistoricalEligibilitySelection

GRAPHS = Path(__file__).parents[2] / "fixtures" / "composite-historical-graphs"


def graph_example(definition="v1", step="root", published=False, *, source_graph=None):
    phase = "published" if published else "evaluated-only"
    graph = (
        source_graph
        if source_graph is not None
        else json.loads((GRAPHS / f"definition-{definition}.{step}-{phase}.json").read_bytes())
    )
    proposal = graph["evaluation_proposal"]
    pin = {
        "product_version": proposal["product_version"],
        "evidence_kind": "PUBLISHED" if published else "EVALUATED_ONLY",
        "month": proposal["evaluation"]["month"],
        "evaluation_revision": proposal["evaluation_revision"],
        "proposal_content_hash": proposal["content_hash"],
        "source_cut_id": proposal["observations"]["source_cut_id"],
        "parent_membership_revision": proposal["parent_membership_revision"],
        "parent_membership_content_hash": proposal["parent_membership_content_hash"],
    }
    receipts = []
    if step != "root":
        for prior in ["correction-2", "root"] if step == "correction-3" else ["root"]:
            receipts.append(
                json.loads(
                    (GRAPHS / f"definition-{definition}.{prior}-published.json").read_bytes()
                )["receipt"]
            )
        pin["lineage_receipts"] = [
            {
                "product_version": r["product_version"],
                "evaluation_revision": r["approval"]["proposal"]["evaluation_revision"],
                "approval_content_hash": r["approval"]["content_hash"],
                "receipt_content_hash": r["content_hash"],
                "receipt_response_digest": response_digest(r),
            }
            for r in receipts
        ]
    month = {"evidence_kind": pin["evidence_kind"], "lineage_receipts": receipts}
    if published:
        receipt, member, universe, parent = (
            graph[k]
            for k in ("receipt", "published_membership", "published_universe", "parent_membership")
        )
        publication = next(
            p
            for p in graph["publications"]["items"]
            if p["sequence"] == receipt["publication_sequence"]
        )
        month.update(
            receipt=receipt,
            membership=member,
            universe=universe,
            parent_membership=parent,
            publication=publication,
        )
        pin.update(
            membership_revision=member["membership_revision"],
            membership_content_hash=member["content_hash"],
            attestation_version=universe["attestation_version"],
            universe_content_hash=universe["content_hash"],
            approval_content_hash=receipt["approval"]["content_hash"],
            receipt_content_hash=receipt["content_hash"],
            publication_sequence=receipt["publication_sequence"],
        )
        if step != "root":
            month["parent_publication"] = next(
                p
                for p in graph["publications"]["items"]
                if p["sequence"] == proposal["amendment"]["expected_current_publication_sequence"]
            )
        else:
            month["parent_publication"] = None
    else:
        month["proposal"] = proposal
    month["response_digests"] = {
        key: response_digest(value) for key, value in month.items() if isinstance(value, dict)
    }
    pin.update(
        {
            ("parent" if key == "parent_membership" else key) + "_response_digest": value
            for key, value in month["response_digests"].items()
        }
    )
    selected = HistoricalEligibilitySelection.model_validate(
        {
            "selection_version": "v3",
            **{
                key: graph["definition"][key]
                for key in ("tenant_id", "composite_id", "definition_version", "reporting_currency")
            },
            "period_start": pin["month"] + "-01",
            "period_end": pin["month"]
            + f"-{calendar.monthrange(int(pin['month'][:4]), int(pin['month'][5:]))[1]:02d}",
            "months": [pin],
        }
    )
    return selected, [month]


def two_month_historical_example(published=True):
    folder = GRAPHS.parent / "composite-historical-two-month"
    first, first_months = graph_example(
        published=True, source_graph=json.loads((folder / "2026-09.published.json").read_bytes())
    )
    phase = "published" if published else "evaluated-only"
    second, second_months = graph_example(
        published=published,
        source_graph=json.loads((folder / f"2026-10.{phase}.json").read_bytes()),
    )
    selected = HistoricalEligibilitySelection.model_validate(
        {
            **first.model_dump(mode="json"),
            "period_end": second.period_end.isoformat(),
            "months": [
                *first.model_dump(mode="json")["months"],
                *second.model_dump(mode="json")["months"],
            ],
        }
    )
    return selected, first_months + second_months
