"""Snapshot lifecycle metadata - stated, never enforced (report#283).

The snapshot's lifecycle claim names the governing policy reference, the
reproduction availability this capture provides, and the responsible
lifecycle authority interface - per the audit's finding 4. It implements
NO retention automation: lotus-archive owns document retention, hold, and
purge, and no second engine exists here. The governed statement lives in
``contracts/report-input-snapshot/lotus-report-input-snapshot-lifecycle.v1.json``.
"""

from __future__ import annotations

SNAPSHOT_LIFECYCLE_POLICY_REF = "report-input-snapshot-standard"
SNAPSHOT_LIFECYCLE_POLICY_VERSION = "1.0.0"
SNAPSHOT_LIFECYCLE_AUTHORITY = (
    "lotus-archive:documents for archived-document retention, hold, purge, "
    "and access audit; lotus-report:report_input_snapshot for snapshot "
    "custody under this policy"
)


def snapshot_lifecycle_claim(*, capture_failed: bool) -> dict[str, str]:
    """The lifecycle metadata stamped on one captured snapshot.

    A successful capture supports rerendering from the immutable snapshot;
    a failed capture records failure evidence only and supports no
    reproduction - stated, never implied away.
    """

    return {
        "policy_ref": SNAPSHOT_LIFECYCLE_POLICY_REF,
        "policy_version": SNAPSHOT_LIFECYCLE_POLICY_VERSION,
        "reproduction_availability": "none" if capture_failed else "rerender_from_snapshot",
        "lifecycle_authority": SNAPSHOT_LIFECYCLE_AUTHORITY,
    }
