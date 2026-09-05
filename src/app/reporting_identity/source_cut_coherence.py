"""Source-cut coherence - one independently defensible trust claim.

Answers exactly one question about a captured report: do the source cuts
the participating sources STATED belong to the same business date as the
report claims? It is deliberately separate from coverage (did sources
state revision evidence at all), reconciliation (did a policy verify the
figures), completeness, and data quality - the 2026-09-05 audit requires
each claim to stand alone.

Like coverage, the verdict is POLICY-DERIVED from the stated evidence, so
it never participates in the revision preimage (the rrv3 lesson: any
policy-derived value inside an identity preimage makes identity
policy-dependent). It is persisted beside the snapshot as its own fact,
under its own policy version tag.

Vocabulary, fail-closed:

- ``coherent``: every source that stated an as-of date stated the
  report's business date, and at least one source stated one.
- ``incoherent``: at least one source stated a DIFFERENT as-of date -
  the offenders are named in the detail, never averaged away.
- ``unevaluable``: no participating source stated an as-of date at all;
  absence of evidence is stated as absence, never graded coherent.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.reporting_identity.identity import SourceRevisionVector

#: Bump when the evaluation rule changes; persisted beside every verdict
#: so historical claims stay attributable to the policy that made them.
SOURCE_CUT_COHERENCE_POLICY_VERSION = "scv1"


class SourceCutCoherence(BaseModel):
    """The evaluated verdict, persisted verbatim beside the snapshot."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str
    policy_version: str
    detail: str


def evaluate_source_cut_coherence(
    *,
    source_revisions: SourceRevisionVector,
    business_as_of_date: str,
) -> SourceCutCoherence:
    """Evaluate whether the stated source cuts share the report's date."""

    stated: list[tuple[str, str]] = [
        (revision.source_service, revision.as_of_date)
        for revision in source_revisions.revisions
        if isinstance(revision.as_of_date, str) and revision.as_of_date.strip()
    ]
    if not stated:
        return SourceCutCoherence(
            status="unevaluable",
            policy_version=SOURCE_CUT_COHERENCE_POLICY_VERSION,
            detail="No participating source stated an as-of date.",
        )
    offenders = sorted(
        {f"{service}={as_of}" for service, as_of in stated if as_of != business_as_of_date}
    )
    if offenders:
        return SourceCutCoherence(
            status="incoherent",
            policy_version=SOURCE_CUT_COHERENCE_POLICY_VERSION,
            detail=(
                f"Stated cuts differ from the report business date "
                f"{business_as_of_date}: " + ", ".join(offenders)
            ),
        )
    return SourceCutCoherence(
        status="coherent",
        policy_version=SOURCE_CUT_COHERENCE_POLICY_VERSION,
        detail=(f"Every stated source cut matches the report business date {business_as_of_date}."),
    )
