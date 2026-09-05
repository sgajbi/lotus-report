"""Bind canonical revision identity to one captured snapshot (report#283).

This is the single place that turns a report job plus its captured payload
into a minted :class:`ReportRevisionIdentity`:

- the series key is built from the job's durable REQUEST facts only - what
  was ordered, never what happened during capture;
- the source revision vector carries only evidence a source actually
  stated (``sourceProduct`` blocks, the accepted advisor-brief run id); a
  participating source that stated nothing appears as a bare entry so the
  absence is explicit, and coverage is computed from the evidence, never
  asserted;
- a failed capture records no facts and mints NO revision.

Report-measured facts about upstream calls (response hashes, latencies)
are deliberately NOT projected into the vector: they are capture evidence,
already persisted on the upstream-call records, not source revision claims.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.report_ordering_catalogue.template_resolution import resolve_report_family
from app.reporting_identity.identity import (
    ReportRevisionIdentity,
    ReportSeriesKey,
    SourceRevision,
    SourceRevisionVector,
    derive_report_revision,
    factual_content_digest,
)

if TYPE_CHECKING:
    from app.reporting_jobs.models import ReportJobLedgerRecord

#: Payload keys whose ``sourceProduct`` block carries source-stated revision
#: evidence, with the block's field names mapped onto SourceRevision fields.
_SOURCE_PRODUCT_SECTIONS = ("incomeAndActivity", "holdings", "transactions")

_SOURCE_PRODUCT_FIELD_MAP = (
    ("product_name", "source_product"),
    ("product_version", "source_product_version"),
    ("as_of_date", "as_of_date"),
    ("generated_at", "generated_at"),
    ("snapshot_id", "source_snapshot_id"),
    ("content_hash", "content_hash"),
    ("restatement_version", "restatement_version"),
    ("source_batch_fingerprint", "source_batch_fingerprint"),
    ("reconciliation_status", "reconciliation_state"),
)

#: Request option keys hoisted into dedicated series-key fields; everything
#: else in the accepted options dict remains an output-affecting semantic
#: option verbatim, so one fact lives in exactly one place.
_HOISTED_OPTION_KEYS = frozenset({"sections", "benchmark_code"})


def series_key_for_job(job: "ReportJobLedgerRecord") -> ReportSeriesKey:
    """The logical series identity of the job's accepted request."""

    options = dict(job.options)
    sections = tuple(
        str(section) for section in options.get("sections") or () if str(section).strip()
    )
    benchmark_code = _stated_str(options.get("benchmark_code"))
    semantic_options = {
        key: value for key, value in options.items() if key not in _HOISTED_OPTION_KEYS
    }
    return ReportSeriesKey(
        tenant_id=job.tenant_id,
        report_family_id=resolve_report_family(job.report_type).report_family_id,
        report_type=job.report_type,
        portfolio_scope=job.portfolio_scope,
        as_of_date=job.as_of_date.isoformat(),
        reporting_currency=job.reporting_currency,
        benchmark_code=benchmark_code,
        sections=sections,
        semantic_options=semantic_options,
    )


def source_revision_vector_for_capture(
    *,
    snapshot_payload: dict[str, Any],
    upstream_services: tuple[str, ...],
) -> SourceRevisionVector:
    """The per-source stated revision evidence behind one captured payload.

    ``upstream_services`` names every source that participated in the
    capture; a participant that stated no revision evidence is preserved as
    a bare entry - explicit absence, never a fabricated revision.
    """

    stated: list[SourceRevision] = []
    for section_key in _SOURCE_PRODUCT_SECTIONS:
        section = snapshot_payload.get(section_key)
        if not isinstance(section, dict):
            continue
        block = section.get("sourceProduct")
        if not isinstance(block, dict):
            continue
        revision = _revision_from_source_product(block)
        if revision is not None:
            stated.append(revision)
    advisor_revision = _revision_from_advisor_commentary(snapshot_payload)
    if advisor_revision is not None:
        stated.append(advisor_revision)
    bounded_revision = _revision_from_bounded_input(
        snapshot_payload=snapshot_payload,
        upstream_services=upstream_services,
    )
    if bounded_revision is not None:
        stated.append(bounded_revision)

    deduped: dict[str, SourceRevision] = {}
    for revision in stated:
        deduped.setdefault(
            json.dumps(revision.canonical(), sort_keys=True, separators=(",", ":")), revision
        )
    revisions = list(deduped.values())

    evidenced_services = {revision.source_service for revision in revisions}
    for service in sorted(set(upstream_services)):
        if service not in evidenced_services:
            revisions.append(SourceRevision(source_service=service))

    return SourceRevisionVector.from_evidence(
        revisions=tuple(revisions),
        expected_sources=tuple(sorted(set(upstream_services))),
    )


def revision_for_capture(
    *,
    job: "ReportJobLedgerRecord",
    snapshot_payload: dict[str, Any],
    upstream_services: tuple[str, ...],
) -> tuple[ReportRevisionIdentity, SourceRevisionVector] | None:
    """Mint the revision identity for one successful capture.

    Returns ``None`` for a failed capture: a payload recording only the
    failure holds no report facts, and no revision may claim it does.
    """

    if snapshot_payload.get("capture_status") == "failed":
        return None
    vector = source_revision_vector_for_capture(
        snapshot_payload=snapshot_payload,
        upstream_services=upstream_services,
    )
    identity = derive_report_revision(
        series_key=series_key_for_job(job),
        source_revisions=vector,
        factual_content_digest=factual_content_digest(snapshot_payload),
    )
    return identity, vector


def _revision_from_source_product(block: dict[str, Any]) -> SourceRevision | None:
    source_service = _stated_str(block.get("source_service"))
    if source_service is None:
        return None
    fields: dict[str, str] = {}
    for block_key, revision_field in _SOURCE_PRODUCT_FIELD_MAP:
        value = _stated_str(block.get(block_key))
        if value is not None:
            fields[revision_field] = value
    return SourceRevision(source_service=source_service, **fields)


def _revision_from_advisor_commentary(snapshot_payload: dict[str, Any]) -> SourceRevision | None:
    package = snapshot_payload.get("advisor_commentary_package")
    if not isinstance(package, dict) or package.get("status") != "included":
        return None
    # The INCLUDED package states its identity as run_id + content_hash (the
    # accepted-output contract); advisor_brief_run_id exists only on the
    # unavailable shape, which carries no accepted content to evidence.
    run_id = _stated_str(package.get("run_id"))
    if run_id is None:
        return None
    fields: dict[str, str] = {"calculation_run_id": run_id}
    content_hash = _stated_str(package.get("content_hash"))
    if content_hash is not None:
        fields["content_hash"] = content_hash
    return SourceRevision(source_service="lotus-ai", **fields)


#: Keys under which a bounded report-input object (proof pack, outcome
#: review, rebalance wave) states the identity of the served artifact.
_BOUNDED_INPUT_ID_KEYS = ("proof_pack_id", "outcome_review_id", "wave_id")


def _revision_from_bounded_input(
    *,
    snapshot_payload: dict[str, Any],
    upstream_services: tuple[str, ...],
) -> SourceRevision | None:
    """Stated revision evidence of a bounded report-input capture.

    A bounded input IS one source-owned object served whole: it states its
    own content_hash and artifact id at the top level. The revision is
    attributed to the single capture-validated upstream participant - the
    payload's own source claims never decide attribution, so a spoofed
    evidence_ref cannot relabel the source.
    """

    content_hash = _stated_str(snapshot_payload.get("content_hash"))
    if content_hash is None:
        return None
    participants = set(upstream_services)
    if len(participants) != 1:
        return None
    fields: dict[str, str] = {"content_hash": content_hash}
    for id_key in _BOUNDED_INPUT_ID_KEYS:
        artifact_id = _stated_str(snapshot_payload.get(id_key))
        if artifact_id is not None:
            fields["source_snapshot_id"] = artifact_id
            break
    evidence_ref = snapshot_payload.get("evidence_ref")
    if isinstance(evidence_ref, dict):
        source_type = _stated_str(evidence_ref.get("source_type"))
        if source_type is not None:
            fields["source_product"] = source_type
        if "source_snapshot_id" not in fields:
            source_id = _stated_str(evidence_ref.get("source_id"))
            if source_id is not None:
                fields["source_snapshot_id"] = source_id
    return SourceRevision(source_service=participants.pop(), **fields)


def _stated_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None
