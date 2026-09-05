"""Canonical mapping from lotus-ai's bounded refusal reasons onto Report's
section-reason vocabulary (issue #166).

Two surfaces answer "why is there no advisor commentary": the pre-order
availability check (does an accepted brief exist?) and the capture that
composes the section (give me this accepted run). They ask lotus-ai different
questions, but when the answer is the same upstream fact they must give it the
same name. An operator told "not reviewed - go accept a brief" before ordering
and then "not found - hunt for a missing run" after has been told the portfolio
changed, when only the surface did.

They diverged in exactly that way, which is why the mapping lives here once
instead of once per caller. Two copies of a fact are two chances to disagree,
and the disagreement is invisible from inside either copy.

Report owns only this mapping. lotus-ai owns which reason is true.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

#: The advisor has not accepted a narrative for this portfolio, or this run is
#: no longer the accepted one. A review-state truth that retrying cannot
#: change; the operator reviews and accepts a brief.
SECTION_REASON_NOT_REVIEWED = "advisor_brief_not_reviewed"

#: The brief cannot be retrieved at all: unknown run, or output that could not
#: be read back.
SECTION_REASON_NOT_FOUND = "advisor_brief_not_found"

#: The artifact exists and was found, and lotus-ai withheld it on authority
#: grounds - its deterministic output validation never returned VALIDATED. The
#: only refusal about admissibility rather than availability. The operator
#: re-runs the brief so it acquires a verdict.
SECTION_REASON_NOT_VALIDATED = "advisor_brief_not_validated"

#: Accepted briefs exist, but none assert the requested report context. A
#: definitive answer about the context, not a failure to look: the operator
#: corrects the report date or currency, and must NOT be sent to widen a scan.
SECTION_REASON_CONTEXT_MISMATCH = "advisor_brief_context_mismatch"

#: lotus-ai could not PROVE which run answers the request - a saturated
#: candidate scan. The brief likely exists and is valid; the answer is unknown
#: rather than negative. Clears through a narrower report context or a widened
#: bound in lotus-ai.
SECTION_REASON_SOURCE_UNPROVEN = "advisor_brief_source_unproven"

#: The artifact exists but its bytes no longer match what the reviewer
#: accepted - an integrity incident in the source (lotus-ai#328). The
#: reviewed narrative is unavailable and must NEVER be regenerated or
#: substituted; operators investigate the artifact store in lotus-ai. No
#: retry changes the answer.
SECTION_REASON_SOURCE_INTEGRITY_FAILED = "advisor_brief_source_integrity_failed"

#: lotus-ai refused for a reason Report does not recognise. A reason Report
#: cannot interpret is not evidence of a cause it can name, so this never
#: masquerades as a known posture.
SECTION_REASON_SOURCE_REFUSED = "advisor_brief_source_refused"

#: lotus-ai answered 200 with a payload that breaks its own published
#: contract. Not a refusal at all - the opposite - so it has no source reason
#: code and appears here only for vocabulary completeness.
SECTION_REASON_CONTRACT_VIOLATION = "advisor_brief_source_contract_violation"

SOURCE_REASON_TO_SECTION_REASON: Mapping[str, str] = MappingProxyType(
    {
        # Review state: nothing is missing, nothing is broken, and no retry
        # changes the answer.
        "run_not_completed": SECTION_REASON_NOT_REVIEWED,
        "run_not_accepted": SECTION_REASON_NOT_REVIEWED,
        "run_superseded": SECTION_REASON_NOT_REVIEWED,
        "no_accepted_run": SECTION_REASON_NOT_REVIEWED,
        # Admissibility.
        "output_not_validated": SECTION_REASON_NOT_VALIDATED,
        # Definitive answer about the requested context.
        "no_context_match": SECTION_REASON_CONTEXT_MISMATCH,
        # Unknown, not negative.
        "lookup_scan_saturated": SECTION_REASON_SOURCE_UNPROVEN,
        # Genuinely unretrievable.
        "pack_projection_unsupported": SECTION_REASON_NOT_FOUND,
        "output_artifact_missing": SECTION_REASON_NOT_FOUND,
        "output_artifact_malformed": SECTION_REASON_NOT_FOUND,
        # Integrity: the bytes changed after acceptance. Unavailable, never
        # regenerated - and deliberately NOT "not found": sending operators
        # hunting for a missing run would misname an integrity incident.
        "output_artifact_integrity_mismatch": SECTION_REASON_SOURCE_INTEGRITY_FAILED,
    }
)


def section_reason_for(source_reason: str | None) -> str | None:
    """Report's name for a lotus-ai refusal reason, or None if unrecognised.

    None is a real answer: it means Report has no interpretation, and the
    caller must say so rather than guess a posture.
    """

    if not source_reason:
        return None
    return SOURCE_REASON_TO_SECTION_REASON.get(source_reason)
