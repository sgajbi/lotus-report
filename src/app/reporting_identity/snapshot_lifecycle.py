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
SNAPSHOT_LIFECYCLE_POLICY_VERSION = "1.1.0"
SNAPSHOT_LIFECYCLE_AUTHORITY = (
    "lotus-archive:documents for archived-document retention, hold, purge, "
    "and access audit; lotus-report:report_input_snapshot for snapshot "
    "custody under this policy"
)


def snapshot_lifecycle_claim(*, capture_failed: bool) -> dict[str, str]:
    """The lifecycle metadata stamped on one captured snapshot.

    The claim states what the SNAPSHOT holds, never what commands exist:
    a successful capture supports recomposing the exact document semantics
    and answering what was presented; a failed capture records failure
    evidence only. The executable rerender COMMAND is a separate,
    lifecycle-dependent fact (archived PDF only - rerender_eligible in the
    rerender service) surfaced at readback, because a capture-time stamp
    can never truthfully promise a command whose eligibility the job has
    not yet earned. Policy 1.0.0 stamped "rerender_from_snapshot" here;
    rows carrying it are read as the 1.1.0 capability claim - stored
    history is never rewritten.
    """

    return {
        "policy_ref": SNAPSHOT_LIFECYCLE_POLICY_REF,
        "policy_version": SNAPSHOT_LIFECYCLE_POLICY_VERSION,
        "reproduction_availability": ("none" if capture_failed else "snapshot_recomposition"),
        "lifecycle_authority": SNAPSHOT_LIFECYCLE_AUTHORITY,
    }


def read_reproduction_availability(stored: object) -> str | None:
    """Read a stored reproduction_availability as the 1.1.0 vocabulary.

    Policy 1.0.0 stamped "rerender_from_snapshot" for the same snapshot
    capability that 1.1.0 names "snapshot_recomposition"; the contract says
    those rows READ AS the capability claim while the stored bytes are never
    rewritten - so the translation lives here, at readback, the only place
    the legacy spelling may still appear.
    """

    if not isinstance(stored, str):
        return None
    return "snapshot_recomposition" if stored == "rerender_from_snapshot" else stored
