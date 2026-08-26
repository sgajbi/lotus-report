from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from check_monetary_float_usage import SELF_PATH, load_allowlist, scan_repo  # noqa: E402

ALLOWLIST_PATH = REPO_ROOT / "docs/standards/monetary-float-allowlist.json"

# The exact template the guard writes into every allowlist entry. It contains no monetary
# float: "migrate" satisfies the "rate" keyword and "monetary float usage" satisfies the
# float pattern, so a substring scan reports it. The guard must never report its own template.
JUSTIFICATION_TEMPLATE = (
    '"justification": "Temporary approved monetary float usage; migrate to Decimal.",'
)


def test_guard_does_not_report_findings_from_its_own_source() -> None:
    findings = scan_repo(REPO_ROOT)

    offending = [item for item in findings if item.startswith("scripts/check_monetary_float_usage")]
    assert offending == [], (
        "The guard scanned its own source. Its keyword vocabulary and justification template "
        "guarantee false positives there."
    )


def test_the_justification_template_is_matched_by_the_scan_rule(tmp_path) -> None:
    """Pin why the exclusion is needed, so nobody removes it as unnecessary."""

    module = tmp_path / "src" / "not_the_guard.py"
    module.parent.mkdir(parents=True)
    module.write_text(f"TEMPLATE = '{JUSTIFICATION_TEMPLATE}'\n", encoding="utf-8")

    findings = scan_repo(tmp_path)

    assert any("not_the_guard.py" in item for item in findings), (
        "The scan rule no longer matches the justification template. If keyword matching "
        "gained word boundaries, revisit the self-exclusion and this test together."
    )


def test_guard_source_is_excluded_by_identity_not_by_a_hardcoded_path() -> None:
    assert SELF_PATH == (REPO_ROOT / "scripts" / "check_monetary_float_usage.py").resolve()


def test_allowlist_holds_no_entry_for_the_guard_itself() -> None:
    payload = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))

    guard_entries = [
        entry
        for entry in payload["allowlist"]
        if entry["finding"].startswith("scripts/check_monetary_float_usage.py:")
    ]

    assert guard_entries == [], (
        "A finding inside the guard itself was allowlisted. Allowlist entries must describe "
        "real monetary floats that somebody has to fix."
    )


def test_every_allowlisted_finding_is_still_produced_by_the_scan() -> None:
    """An allowlist entry that no longer matches anything is stale approval, not coverage."""

    findings = set(scan_repo(REPO_ROOT))
    allowlist_entries, errors, stale = load_allowlist(ALLOWLIST_PATH)

    assert errors == []
    assert stale == []
    orphaned = sorted(set(allowlist_entries) - findings)
    assert orphaned == [], f"Allowlist entries no longer matched by the scan: {orphaned}"
