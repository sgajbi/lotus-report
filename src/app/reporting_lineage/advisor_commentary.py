"""Capture-time resolution of the ADVISOR_COMMENTARY report section (issue #166).

The section is sourced exclusively from the lotus-ai accepted-output projection
for `advisor_brief.pack@v1` runs, resolved by the run id the caller named on the
report order. lotus-report composes that source-owned, human-accepted truth and
never regenerates, edits, or re-reviews narrative content; AI content enters the
client document only with the accepting review identity and audit lineage
attached.

Failure semantics follow the report's section-vs-job split:

- Definitive source postures (run not accepted, superseded, unknown run,
  missing or malformed output artifact) and report-side context mismatches
  close the SECTION with a bounded reason code; the report job proceeds and
  the document truthfully omits the commentary.
- Transport-level unavailability (network failure, lotus-ai 5xx) fails the
  CAPTURE with the standard retryable upstream posture - retrying can succeed,
  so silently shipping the pack without a section the caller paid for would be
  publishing an incomplete report.
"""

from typing import Any, Protocol

ADVISOR_COMMENTARY_SECTION_ID = "ADVISOR_COMMENTARY"
ADVISOR_BRIEF_ACCEPTED_OUTPUT_SCHEMA_ID = (
    "lotus-ai.workflow_pack_run.accepted_output.advisor_brief.v1"
)
NARRATIVE_TONES = frozenset({"positive", "neutral", "warning"})

# lotus-ai's bounded refusal reasons, mapped exhaustively to report section
# postures. Mapped by `metadata.reason_code` rather than by parsing the
# error_code string, because the bare code is the stable part of that contract.
#
# "This run is not (or is no longer) the accepted narrative" - a review-state
# truth that retrying cannot change.
_NOT_REVIEWED_SOURCE_REASONS = frozenset(
    {"run_not_completed", "run_not_accepted", "run_superseded"}
)
# The brief cannot be retrieved at all.
_NOT_FOUND_SOURCE_REASONS = frozenset(
    {
        "pack_projection_unsupported",
        "output_artifact_missing",
        "output_artifact_malformed",
        "no_accepted_run",
    }
)
# The artifact EXISTS and was FOUND, and lotus-ai withheld it on authority
# grounds: its deterministic output validation never returned VALIDATED
# (lotus-ai#231). Distinct from every other reason because those are about
# availability and this one is about admissibility - and because the operator
# action differs: re-run the brief so it acquires a verdict, rather than hunt
# for a missing run. For a governed client document, "withheld because its
# authority was never established" is an audit-relevant statement that
# "not found" would erase.
_NOT_VALIDATED_SOURCE_REASONS = frozenset({"output_not_validated"})
# The source could not prove which run answers the request - the brief very
# likely exists and is valid. Retryable, unlike every reason above, and
# lotus-ai deliberately excludes it from its own not-found set.
_UNPROVEN_SOURCE_REASONS = frozenset({"lookup_scan_saturated", "no_context_match"})


class AdvisorCommentarySourceUnavailableError(RuntimeError):
    """Transport-level failure reaching lotus-ai; the capture must retry."""


class AcceptedOutputClient(Protocol):
    async def get_accepted_workflow_output(
        self,
        run_id: str,
        *,
        tenant_id: str,
    ) -> tuple[int, dict[str, Any]]: ...


def advisor_commentary_requested(options: dict[str, Any]) -> bool:
    sections = options.get("sections")
    if not isinstance(sections, list):
        return False
    return any(
        isinstance(item, str) and item.upper() == ADVISOR_COMMENTARY_SECTION_ID for item in sections
    )


def requested_advisor_brief_run_id(options: dict[str, Any]) -> str | None:
    run_id = options.get("advisor_brief_run_id")
    if isinstance(run_id, str) and run_id.strip():
        return run_id.strip()
    return None


async def resolve_advisor_commentary_package(
    *,
    client: AcceptedOutputClient,
    run_id: str,
    tenant_id: str,
    portfolio_id: str,
    as_of_date: str,
    reporting_currency: str | None,
    report_period: str | None = None,
    benchmark_code: str | None = None,
) -> dict[str, Any]:
    """Resolve the accepted brief into the bounded section package.

    Returns a package with ``status: "included"`` carrying the exact accepted
    narrative plus provenance, or ``status: "unavailable"`` with one bounded
    reason code. Raises :class:`AdvisorCommentarySourceUnavailableError` on
    transport-level failures so the capture keeps its retryable posture.
    """

    status_code, payload = await _fetch_accepted_output(
        client=client, run_id=run_id, tenant_id=tenant_id
    )
    if status_code != 200:
        return _unavailable(run_id, _map_source_reason(status_code, payload))

    identity_fault = _projection_identity_fault(payload, run_id=run_id, tenant_id=tenant_id)
    if identity_fault is not None:
        return _unavailable(run_id, "advisor_brief_not_found", detail=identity_fault)

    context = _as_dict_value(payload.get("context"))
    mismatch = _context_mismatch(
        context,
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        reporting_currency=reporting_currency,
        report_period=report_period,
        benchmark_code=benchmark_code,
    )
    if mismatch is not None:
        return _unavailable(run_id, "advisor_brief_context_mismatch", detail=mismatch)

    review = _as_dict_value(payload.get("review"))
    reviewed_by = _clean_str(review.get("reviewed_by"))
    reviewed_at = _clean_str(review.get("reviewed_at"))
    content_hash = _clean_str(payload.get("content_hash"))
    source_refs = _string_list(payload.get("source_refs"))
    if not reviewed_by or not reviewed_at or not content_hash or not source_refs:
        # Without the accepting reviewer identity, the pinned content hash, or
        # the grounding source references, the mandated AI-disclosure
        # provenance line cannot be rendered truthfully - it names the
        # reviewer, the run and the sources - so the section closes rather
        # than shipping narrative under an unverifiable disclosure.
        return _unavailable(run_id, "ai_disclosure_policy_unavailable")

    return _included_package(
        payload,
        run_id=run_id,
        portfolio_id=portfolio_id,
        context=context,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        content_hash=content_hash,
    )


async def _fetch_accepted_output(
    *,
    client: AcceptedOutputClient,
    run_id: str,
    tenant_id: str,
) -> tuple[int, dict[str, Any]]:
    try:
        status_code, payload = await client.get_accepted_workflow_output(
            run_id, tenant_id=tenant_id
        )
    except Exception as exc:
        raise AdvisorCommentarySourceUnavailableError(
            f"lotus-ai accepted-output retrieval failed for run {run_id}."
        ) from exc
    if status_code >= 500 or status_code == 429:
        raise AdvisorCommentarySourceUnavailableError(
            f"lotus-ai accepted-output returned {status_code} for run {run_id}."
        )
    if status_code in {401, 403}:
        # The lotus-report caller is missing or inactive in the lotus-ai
        # registry: an environment-wide deployment fault, not a run posture.
        # Closing the section would mask it as a missing brief on every order.
        raise AdvisorCommentarySourceUnavailableError(
            f"lotus-ai refused the lotus-report caller ({status_code}) for run "
            f"{run_id}; check the lotus-ai access-control registry."
        )
    return status_code, payload


def _included_package(
    payload: dict[str, Any],
    *,
    run_id: str,
    portfolio_id: str,
    context: dict[str, Any],
    reviewed_by: str,
    reviewed_at: str,
    content_hash: str,
) -> dict[str, Any]:
    return {
        "status": "included",
        "schema_id": ADVISOR_BRIEF_ACCEPTED_OUTPUT_SCHEMA_ID,
        "run_id": run_id,
        "pack_id": _text(payload.get("pack_id")),
        "pack_version": _text(payload.get("pack_version")),
        "task_id": _text(payload.get("task_id")),
        "request_id": _text(payload.get("request_id")),
        "workflow_authority_owner": _text(payload.get("workflow_authority_owner")),
        "review": {"reviewed_by": reviewed_by, "reviewed_at": reviewed_at},
        "advisor_brief_status": _text(payload.get("advisor_brief_status")),
        "coverage_state": _text(payload.get("coverage_state")),
        "grounded_summary": _text(payload.get("grounded_summary"), ""),
        "talking_points": _narrative_items(payload.get("talking_points")),
        "risks_and_exceptions": _narrative_items(payload.get("risks_and_exceptions")),
        "context": {
            "portfolio_id": _text(context.get("portfolio_id"), portfolio_id),
            "period": _text(context.get("period")),
            "as_of_date": _clean_str(context.get("as_of_date")),
            "reporting_currency": _clean_str(context.get("reporting_currency")),
            "benchmark": _clean_str(context.get("benchmark")),
        },
        "source_refs": _string_list(payload.get("source_refs")),
        "evidence_types": _string_list(payload.get("evidence_types")),
        "content_hash": content_hash,
        "content_hash_algorithm": _text(payload.get("content_hash_algorithm"), "sha256"),
        "notes": _string_list(payload.get("notes")),
        "disclosure_text": (
            "Commentary generated with AI assistance and reviewed by "
            f"{reviewed_by} on {reviewed_at}; run {run_id}."
        ),
    }


def _unavailable(run_id: str, reason_code: str, *, detail: str | None = None) -> dict[str, Any]:
    package: dict[str, Any] = {
        "status": "unavailable",
        "reason_code": reason_code,
        "advisor_brief_run_id": run_id,
    }
    if detail:
        package["detail"] = detail
    return package


def _projection_identity_fault(
    payload: dict[str, Any], *, run_id: str, tenant_id: str
) -> str | None:
    """A 200 body must carry the exact contract identity: composing narrative
    from a different schema, or for a different run, would archive it under
    fabricated provenance."""

    schema_id = _clean_str(payload.get("schema_id"))
    if schema_id != ADVISOR_BRIEF_ACCEPTED_OUTPUT_SCHEMA_ID:
        return f"unexpected accepted-output schema_id {schema_id or 'missing'}"
    payload_run_id = _clean_str(payload.get("run_id"))
    if payload_run_id != run_id:
        return f"accepted-output run_id {payload_run_id or 'missing'} != requested {run_id}"
    payload_tenant = _clean_str(payload.get("tenant_id"))
    if payload_tenant != tenant_id:
        # lotus-ai is tenant-scoped and fails closed cross-tenant, so this
        # should be unreachable - which is exactly why it is asserted rather
        # than assumed: composing one tenant's reviewed narrative into another
        # tenant's client document is the worst outcome this section has.
        return f"accepted-output tenant {payload_tenant or 'missing'} != requested {tenant_id}"
    return None


def _map_source_reason(status_code: int, payload: dict[str, Any]) -> str:
    """One report posture per lotus-ai refusal, with an honest fall-through.

    The unmapped case used to return `advisor_brief_not_found`, which asserted
    a cause the source never claimed: an operator was sent hunting for a
    missing run when the brief existed and had been withheld for a specific
    governed reason. That default is also what let the gap live - an unmapped
    code was indistinguishable from a handled one.

    A reason this service cannot interpret is not evidence of a cause it can
    name, so unknown refusals now say exactly that. The failure mode of
    lotus-ai adding a reason before Report maps it is a visible
    "source refused, reason unrecognised", not a confident wrong answer.
    """

    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    reason = _clean_str(metadata.get("reason_code")) or ""
    if reason in _NOT_REVIEWED_SOURCE_REASONS:
        return "advisor_brief_not_reviewed"
    if reason in _NOT_VALIDATED_SOURCE_REASONS:
        return "advisor_brief_not_validated"
    if reason in _UNPROVEN_SOURCE_REASONS:
        return "advisor_brief_source_unproven"
    if reason in _NOT_FOUND_SOURCE_REASONS:
        return "advisor_brief_not_found"
    if not reason and status_code == 404:
        # An unadorned 404 is the one status that means absence on its own.
        return "advisor_brief_not_found"
    return "advisor_brief_source_refused"


def _context_mismatch(
    context: dict[str, Any],
    *,
    portfolio_id: str,
    as_of_date: str,
    reporting_currency: str | None,
    report_period: str | None,
    benchmark_code: str | None,
) -> str | None:
    """A null source-context value means "not asserted by the source" and never
    conflicts; only definite disagreements close the section."""

    source_portfolio = _clean_str(context.get("portfolio_id"))
    if source_portfolio and source_portfolio != portfolio_id:
        return f"brief portfolio {source_portfolio} != report portfolio {portfolio_id}"
    source_as_of = _clean_str(context.get("as_of_date"))
    if source_as_of and source_as_of != as_of_date:
        return f"brief as_of_date {source_as_of} != report as_of_date {as_of_date}"
    source_currency = _clean_str(context.get("reporting_currency"))
    if source_currency and reporting_currency and source_currency != reporting_currency:
        return (
            f"brief reporting_currency {source_currency} != "
            f"report reporting_currency {reporting_currency}"
        )
    source_period = _clean_str(context.get("period"))
    if source_period and report_period and source_period != report_period:
        # A brief written about YTD does not describe a Q3 report, however
        # accurate it is about YTD.
        return f"brief period {source_period} != report period {report_period}"
    source_benchmark = _clean_str(context.get("benchmark"))
    if source_benchmark and benchmark_code and source_benchmark != benchmark_code:
        # Relative-performance narrative is only true of the benchmark it was
        # written against.
        return f"brief benchmark {source_benchmark} != report benchmark {benchmark_code}"
    return None


def _narrative_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        tone = _clean_str(item.get("tone")) or "neutral"
        items.append(
            {
                "headline": _clean_str(item.get("headline")) or "",
                "detail": _clean_str(item.get("detail")) or "",
                "tone": tone if tone in NARRATIVE_TONES else "neutral",
                "evidence_refs": _evidence_refs(item.get("evidence_refs")),
            }
        )
    return items


def _evidence_refs(value: Any) -> list[dict[str, str]]:
    """Grounding refs as lotus-ai's AdvisorBriefAcceptedEvidenceRef shape
    (lotus-ai#189): {metric_label, metric_value, source_ref}, all required.
    The source projector fails closed on any other shape, so report drops
    non-conforming entries rather than inventing partial grounding."""

    if not isinstance(value, list):
        return []
    refs: list[dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        metric_label = _clean_str(entry.get("metric_label"))
        metric_value = _clean_str(entry.get("metric_value"))
        source_ref = _clean_str(entry.get("source_ref"))
        if not metric_label or not metric_value or not source_ref:
            continue
        refs.append(
            {
                "metric_label": metric_label,
                "metric_value": metric_value,
                "source_ref": source_ref,
            }
        )
    return refs


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _text(value: Any, default: str = "not_available") -> str:
    return _clean_str(value) or default


def _as_dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
