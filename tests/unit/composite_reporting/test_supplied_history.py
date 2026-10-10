"""Supplied history joins exact financial pins without monthly-policy approvals."""

from copy import deepcopy

import jsonschema
import pytest

from app.composite_reporting.admission import admit_composite_response, response_digest
from app.composite_reporting.eligibility_admission import source_hash
from app.composite_reporting.models import CompositeSourceContextSelection
from app.composite_reporting.source_context_contract import (
    CompositeSourceContextReportData,
    project_source_context,
    source_context_report_schema,
)
from tests.unit.composite_reporting.supplied_history_examples import supplied_history_example


def history_dataset(corrected=False, include_si=False):
    selection, source, definition, context, captures = supplied_history_example(
        corrected, include_si
    )
    primary = admit_composite_response(
        selection=selection, admitted_tenant_id=selection.tenant_id, status_code=200, payload=source
    )
    return project_source_context(primary, context, definition, captures)


@pytest.mark.parametrize("corrected", [False, True])
@pytest.mark.parametrize("include_si", [False, True])
def test_exact_full_intervals_parent_window_and_optional_si(corrected, include_si):
    data = history_dataset(corrected, include_si)
    CompositeSourceContextReportData.model_validate(data)
    jsonschema.validate(data, source_context_report_schema())
    table = next(t for t in data["tables"] if t["table_id"] == "MembershipHistory")
    expected = [
        decision
        for capture in data["supplied_memberships"]
        for decision in capture["source_response"]["decisions"]
    ]
    assert len(table["rows"]) == len(expected)
    for row, decision in zip(table["rows"], expected, strict=True):
        for field in (
            "portfolio_id",
            "effective_from",
            "effective_to",
            "status",
            "reason_code",
            "approval_ref",
            "source_snapshot_id",
        ):
            assert row["cells"][field]["canonical_value"] == decision[field]
        assert row["cells"]["discretionary"]["canonical_value"] == "true"
    c = [r for r in table["rows"] if r["cells"]["portfolio_id"]["canonical_value"] == "C"]
    assert all(row["cells"]["effective_to"]["canonical_value"] == "2026-01-15" for row in c)
    assert all(row["cells"]["status"]["canonical_value"] == "INCLUDED" for row in c)
    b = [
        r for r in table["rows"] if r["cells"]["effective_from"]["canonical_value"] == "2026-02-01"
    ]
    assert all(
        r["cells"]["reason_code"]["canonical_value"] == "SYNTHETIC_POLICY_EXCLUSION" for r in b
    )
    if corrected:
        corrected_rows = table["rows"][4:]
        assert all(
            r["cells"]["supersedes_membership_revision"]["canonical_value"] == "m1"
            for r in corrected_rows
        )
        assert all(
            r["cells"]["affected_from"]["canonical_value"] == "2026-01-05" for r in corrected_rows
        )
        assert all(
            r["cells"]["affected_to"]["canonical_value"] == "2026-01-05" for r in corrected_rows
        )
    assert ("SinceInception" in {t["table_id"] for t in data["tables"]}) == include_si
    if include_si:
        assert data["report_facts"]["since_inception"]["value"] is None


@pytest.mark.parametrize(
    "mutation", ["drop", "foreign", "hash", "digest", "parent", "interval", "unreferenced"]
)
def test_history_source_and_join_corruption_refused(mutation):
    data = history_dataset(True)
    if mutation == "drop":
        data["supplied_memberships"].pop()
        data["source_context"]["memberships"].pop()
    elif mutation == "unreferenced":
        data["selection"]["windows"][0]["membership_content_hash"] = "sha256:" + "a" * 64
    else:
        capture = data["supplied_memberships"][1]
        raw = capture["source_response"]
        if mutation == "foreign":
            raw["tenant_id"] = "foreign"
        if mutation == "hash":
            raw["source_cut_id"] = "changed"
        if mutation == "digest":
            capture["source_response_digest"] = "sha256:" + "a" * 64
        if mutation == "parent":
            raw["supersedes_membership_revision"] = "missing"
        if mutation == "interval":
            raw["decisions"][0]["effective_to"] = "2025-01-01"
    with pytest.raises(ValueError):
        CompositeSourceContextReportData.model_validate(data)


@pytest.mark.parametrize("mutation", ["end", "closed", "null", "pointer", "omission"])
def test_history_projection_cannot_fabricate_closure_or_omit_retained_intervals(mutation):
    data = history_dataset()
    table = next(t for t in data["tables"] if t["table_id"] == "MembershipHistory")
    row = table["rows"][-1]
    if mutation == "end":
        row["cells"]["effective_to"]["canonical_value"] = "2026-02-28"
    if mutation == "closed":
        row["cells"]["status"]["canonical_value"] = "CLOSED"
    if mutation == "null":
        table["rows"][0]["cells"]["effective_to"].update(
            availability="UNKNOWN", reason_codes=["FAKE"]
        )
    if mutation == "pointer":
        row["cells"]["portfolio_id"]["source_pointer"] = (
            "/supplied_memberships/0/source_response/decisions/0/portfolio_id"
        )
    if mutation == "omission":
        table["rows"].pop()
    with pytest.raises(ValueError):
        CompositeSourceContextReportData.model_validate(data)


def test_parent_and_interval_guards_after_hash_and_all_pins_are_updated():
    from app.composite_reporting.membership_history import require_membership_join

    selection, _, definition, _, captures = supplied_history_example(True)
    for mutation in ("parent", "interval"):
        invalid = deepcopy(captures)
        raw = invalid[1].source_response
        if mutation == "parent":
            raw["supersedes_membership_revision"] = "missing"
        else:
            raw["decisions"][0]["effective_to"] = "2025-01-01"
        raw["content_hash"] = source_hash(raw)
        invalid[1].source_response_digest = response_digest(raw)
        invalid[1].pin.content_hash = raw["content_hash"]
        invalid[1].pin.response_digest = response_digest(raw)
        selected = selection.model_copy(deep=True)
        selected.windows[0].membership_content_hash = raw["content_hash"]
        with pytest.raises(ValueError, match="PARENT_CONFLICT|INTERVAL_INVALID"):
            require_membership_join(
                selected, definition.source_response, definition.endpoint, invalid
            )


def test_empty_context_and_duplicate_revision_are_refused():
    _, _, definition, context, _ = supplied_history_example()
    with pytest.raises(ValueError, match="ATTACHMENT_REQUIRED"):
        CompositeSourceContextSelection(definition=definition.pin)
    with pytest.raises(ValueError, match="REVISION_DUPLICATED"):
        CompositeSourceContextSelection(
            definition=definition.pin, memberships=context.memberships * 2
        )


def test_valid_but_unreferenced_correction_cannot_be_attached():
    from app.composite_reporting.membership_history import require_membership_join

    selection, _, definition, _, captures = supplied_history_example(True)
    selection.windows[0].membership_content_hash = captures[0].pin.content_hash
    with pytest.raises(ValueError, match="ATTACHMENT_UNREFERENCED"):
        require_membership_join(
            selection, definition.source_response, definition.endpoint, captures
        )
