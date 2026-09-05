"""Canonical report series and revision identity (report#283, identity v2).

Three typed identities close the ambiguity the v1 evidence labels carried:

- ``ReportSeriesKey`` identifies the LOGICAL requested report - the tenant,
  family, scope, business date, and every output-affecting semantic option -
  never one execution and never one captured revision.
- ``SourceRevisionVector`` records the source-owned revision evidence each
  participating source actually stated. Missing evidence stays missing:
  nothing here synthesises a revision to fill a field.
- ``ReportRevisionIdentity`` derives a versioned, opaque, deterministic
  report-revision id from the canonical serialization of the series key, the
  source revision vector digest, and the factual-content digest of the
  captured snapshot payload.

Hash boundary (the report#283 audit decision, finding 1):

  Two DISTINCT hashes exist and neither replaces the other.

  - ``snapshot_hash`` (owned by the lineage store) covers the COMPLETE
    inline payload bytes, capture timestamps and transport metadata
    included. It is the capture-INSTANCE integrity hash: it proves the
    stored bytes are the captured bytes. Historical values are never
    recomputed or rewritten.
  - ``factual_content_digest`` (owned here) covers the payload with the
    versioned ``CAPTURE_INSTANCE_KEYS`` removed recursively. It is the
    factual-CONTENT hash: two captures that produced identical reader-
    visible facts share it even though their instance hashes differ.

  Source-stated timestamps inside ``sourceProduct`` blocks share key names
  with capture-instance fields and are therefore also outside the factual
  boundary - deliberately: which source cut served the facts is carried by
  the SourceRevisionVector, which participates in the revision preimage
  separately, so a changed source cut still changes the revision id.

Capture-instance policy: every successful capture is one snapshot row (its
own ``snapshot_hash`` and ``captured_at``); the revision id is derived, not
random, so a re-capture yielding identical facts from identical stated
source revisions IS the same report revision across distinct capture
instances. A failed capture records no facts and mints NO revision. Rows
captured before this identity existed keep NULL revision columns - history
is never relabelled (audit finding 5).

No circular identity (audit finding 2): the revision id is persisted in
side columns beside the payload, never inside it; the digest refuses a
payload that already carries one.

Invariants (each pinned by tests):

  same revision id -> same tenant, same semantic request, same source
  revision vector, same factual snapshot content;
  a restatement or backdated correction that changes source facts ->
  a different report revision;
  a pure rerender or replay-clone of the same snapshot -> the SAME
  report revision.

Coverage-policy note: the qualifying-evidence rule (only
``QUALIFYING_REVISION_EVIDENCE_FIELDS`` establish coverage) tightened the
computed coverage claim after the first rrv2 mints landed. The derivation
scheme (rrv2) and factual boundary (fb1) are unchanged; a snapshot minted
under the looser rule keeps its stored id and its persisted vector shows
verbatim which claim it made - stored identities are references, never
recomputed, and historical snapshots are never rewritten.

The structured fields remain authoritative everywhere; the opaque id is a
stable reference, never a string to parse.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

REPORT_REVISION_ID_VERSION = "rrv2"

#: Version tag of the factual-content boundary below. Bump when the
#: exclusion set changes; the tag participates in the revision preimage so
#: ids minted under different boundaries can never collide silently.
FACTUAL_BOUNDARY_VERSION = "fb1"

#: Capture-instance keys excluded - recursively, by exact name - from the
#: factual-content digest. These record WHEN and THROUGH WHICH REQUEST the
#: facts were captured or served, never WHAT the facts are:
#: ``generated_at`` (composition/serving instants), ``captured_at``
#: (capture instants), ``correlation_id``/``trace_id`` (transport).
#: Everything else in the payload is factual content by definition.
CAPTURE_INSTANCE_KEYS = frozenset({"generated_at", "captured_at", "correlation_id", "trace_id"})

#: Fields a source may state about the revision of the facts it served.
#: Every field is optional because only the source owns this evidence -
#: absence is recorded as absence, never invented.
_SOURCE_REVISION_FIELDS = (
    "source_service",
    "source_product",
    "source_product_version",
    "as_of_date",
    "generated_at",
    "source_snapshot_id",
    "content_hash",
    "restatement_version",
    "source_batch_fingerprint",
    "calculation_run_id",
    "methodology_version",
    "supportability_status",
    "reconciliation_state",
)

#: Fields whose stated value identifies WHICH DATA REVISION supplied the
#: facts, per the source-owner contracts (Core's sourceProduct blocks, the
#: lotus-ai accepted-output projection, the DPM bounded report inputs):
#: a content hash, the source's snapshot/artifact id, a restatement
#: version, a batch fingerprint, a run id, or the stated generation
#: instant of the served cut. Everything else a source may state is
#: preserved verbatim but does NOT qualify: catalogue identity
#: (source_product, source_product_version) says which PRODUCT served,
#: the business as_of_date is request semantics shared by every capture
#: of the series, methodology_version names configuration, and
#: supportability/reconciliation are quality labels - none of them says
#: which data revision the source served.
QUALIFYING_REVISION_EVIDENCE_FIELDS = frozenset(
    {
        "generated_at",
        "source_snapshot_id",
        "content_hash",
        "restatement_version",
        "source_batch_fingerprint",
        "calculation_run_id",
    }
)


class ReportSeriesKey(BaseModel):
    """The logical requested report: who ordered what, about which scope,
    for which business date, with which output-affecting options.

    Fail-closed: an unknown field is rejected, never silently discarded - a
    producer introducing a new output-affecting option before this consumer
    models it must break validation, not keep the old identity. Nested
    request state is deep-copied at admission so later caller-side mutation
    cannot change an already admitted identity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str = Field(min_length=1)
    report_family_id: str = Field(min_length=1)
    report_type: str = Field(min_length=1)
    portfolio_scope: dict[str, Any]
    as_of_date: str = Field(min_length=1)
    reporting_currency: str | None = None
    benchmark_code: str | None = None
    sections: tuple[str, ...] = ()
    allocation_dimensions: tuple[str, ...] = ()
    semantic_options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _detach_nested_state(self) -> "ReportSeriesKey":
        # frozen=True freezes attributes, not the dicts/lists behind them:
        # deep-copy severs aliasing with caller state so mutation after
        # admission cannot alter the identity this model records.
        object.__setattr__(self, "portfolio_scope", copy.deepcopy(self.portfolio_scope))
        object.__setattr__(self, "semantic_options", copy.deepcopy(self.semantic_options))
        return self

    def canonical(self) -> dict[str, Any]:
        """Deterministic canonical form: order-insensitive collections are
        sorted, absent optionals are omitted, values stay verbatim."""

        canonical: dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "report_family_id": self.report_family_id,
            "report_type": self.report_type,
            "portfolio_scope": _canonical_portfolio_scope(self.portfolio_scope),
            "as_of_date": self.as_of_date,
            "sections": sorted(self.sections),
            "allocation_dimensions": sorted(self.allocation_dimensions),
            "semantic_options": _canonical_value(self.semantic_options),
        }
        if self.reporting_currency is not None:
            canonical["reporting_currency"] = self.reporting_currency
        if self.benchmark_code is not None:
            canonical["benchmark_code"] = self.benchmark_code
        return canonical

    def digest(self) -> str:
        return _sha256_of(self.canonical())


class SourceRevision(BaseModel):
    """One source's stated revision evidence, verbatim and possibly partial."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_service: str = Field(min_length=1)
    source_product: str | None = None
    source_product_version: str | None = None
    as_of_date: str | None = None
    generated_at: str | None = None
    source_snapshot_id: str | None = None
    content_hash: str | None = None
    restatement_version: str | None = None
    source_batch_fingerprint: str | None = None
    calculation_run_id: str | None = None
    methodology_version: str | None = None
    supportability_status: str | None = None
    reconciliation_state: str | None = None

    def canonical(self) -> dict[str, Any]:
        # A blank or whitespace value is NOT stated evidence: excluding it
        # here keeps canonical forms honest and stops from_evidence() from
        # declaring coverage complete over empty strings.
        return {
            name: value
            for name in _SOURCE_REVISION_FIELDS
            if (value := getattr(self, name)) is not None
            and (not isinstance(value, str) or value.strip())
        }

    def states_revision_evidence(self) -> bool:
        """True only when a stated field identifies WHICH data revision the
        source served. Catalogue identity and quality labels never do."""

        return any(name in QUALIFYING_REVISION_EVIDENCE_FIELDS for name in self.canonical())


class SourceRevisionVector(BaseModel):
    """The per-source revision evidence behind one captured report.

    ``coverage`` states honestly how much of the vector is evidence-backed:
    ``complete`` only when every participating source stated QUALIFYING
    revision evidence (``QUALIFYING_REVISION_EVIDENCE_FIELDS``); otherwise
    ``partial`` or ``unknown``. Nothing upgrades it, and the claim is
    VALIDATED against the revisions themselves - an enum value alone is
    not evidence, and neither is catalogue identity or a quality label.

    Coverage is ONLY an evidence-presence claim. It is distinct from
    source-cut coherence (whether the stated cuts belong together) and
    from reconciliation (whether a policy verified the figures) - complete
    coverage asserts neither.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    revisions: tuple[SourceRevision, ...] = ()
    coverage: Literal["unknown", "partial", "complete"] = "unknown"

    @model_validator(mode="after")
    def _coverage_must_be_evidence_backed(self) -> "SourceRevisionVector":
        if self.coverage == "complete":
            if not self.revisions:
                raise ValueError(
                    "SOURCE_REVISION_COVERAGE_UNBACKED: an empty vector cannot "
                    "claim complete coverage."
                )
            evidenced_by_service: dict[str, bool] = {}
            for revision in self.revisions:
                evidenced_by_service[revision.source_service] = (
                    evidenced_by_service.get(revision.source_service, False)
                    or revision.states_revision_evidence()
                )
            for service in sorted(evidenced_by_service):
                if not evidenced_by_service[service]:
                    raise ValueError(
                        "SOURCE_REVISION_COVERAGE_UNBACKED: coverage=complete "
                        f"requires qualifying revision evidence for every "
                        f"participating source; {service} stated none - "
                        "catalogue identity and quality labels identify which "
                        "product served, never which data revision."
                    )
        return self

    @classmethod
    def from_evidence(
        cls, *, revisions: tuple[SourceRevision, ...], expected_sources: tuple[str, ...]
    ) -> "SourceRevisionVector":
        """Compute coverage from the evidence instead of trusting a label.

        complete: every expected source stated QUALIFYING revision evidence
        (a content hash, snapshot id, restatement version, batch
        fingerprint, run id, or stated generation instant); partial: some
        did; unknown: none did. Catalogue identity and quality labels never
        qualify - they say which product served, not which data revision.
        """

        evidenced = {
            revision.source_service for revision in revisions if revision.states_revision_evidence()
        }
        expected = set(expected_sources)
        if expected and expected <= evidenced:
            coverage = "complete"
        elif evidenced:
            coverage = "partial"
        else:
            coverage = "unknown"
        return cls(revisions=revisions, coverage=coverage)

    def canonical(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage,
            # Sorted by the COMPLETE canonical value: two revisions that
            # differ in ANY identity field order deterministically, and only
            # byte-identical revisions can tie - so caller-side ordering can
            # never change the digest.
            "revisions": sorted(
                (revision.canonical() for revision in self.revisions),
                key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
            ),
        }

    def digest(self) -> str:
        return _sha256_of(self.canonical())


class ReportRevisionIdentity(BaseModel):
    """The restatement-safe identity of one captured report revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_revision_id: str
    series_digest: str
    source_revision_digest: str
    factual_content_digest: str
    factual_boundary_version: str


def factual_content_digest(snapshot_payload: dict[str, Any]) -> str:
    """Digest of the payload's factual content under the versioned boundary.

    ``CAPTURE_INSTANCE_KEYS`` are removed recursively before canonical
    hashing, so a payload that differs only in capture instants or transport
    metadata digests identically. Refuses a payload that already carries a
    revision id: the id must never be part of its own preimage.
    """

    if "report_revision_id" in snapshot_payload:
        raise ValueError(
            "REPORT_REVISION_CIRCULAR_IDENTITY: the snapshot payload already "
            "carries a report_revision_id; the revision binding lives beside "
            "the payload, never inside its own preimage."
        )
    return f"sha256:{_sha256_of(_factual_content(snapshot_payload))}"


def derive_report_revision(
    *,
    series_key: ReportSeriesKey,
    source_revisions: SourceRevisionVector,
    factual_content_digest: str,
) -> ReportRevisionIdentity:
    """Derive the deterministic report-revision identity.

    The id changes when - and only when - the semantic request, the source
    revision evidence, or the factual snapshot content changes. Capture time
    deliberately does NOT participate: two captures that produced identical
    facts from identical source revisions ARE the same revision, and a pure
    rerender (same snapshot) never mints a new one.
    """

    if not factual_content_digest.strip():
        raise ValueError(
            "REPORT_REVISION_CONTENT_DIGEST_REQUIRED: a report revision "
            "exists only for captured facts; refusing to mint identity "
            "without the factual-content digest."
        )
    canonical = {
        "identity_version": REPORT_REVISION_ID_VERSION,
        "factual_boundary_version": FACTUAL_BOUNDARY_VERSION,
        "series": series_key.canonical(),
        "source_revisions": source_revisions.canonical(),
        "factual_content_digest": factual_content_digest,
    }
    return ReportRevisionIdentity(
        report_revision_id=f"{REPORT_REVISION_ID_VERSION}_{_sha256_of(canonical)}",
        series_digest=series_key.digest(),
        source_revision_digest=source_revisions.digest(),
        factual_content_digest=factual_content_digest,
        factual_boundary_version=FACTUAL_BOUNDARY_VERSION,
    )


def _factual_content(value: Any) -> Any:
    """The payload with capture-instance keys removed at every depth."""

    if isinstance(value, dict):
        return {
            str(key): _factual_content(item)
            for key, item in value.items()
            if str(key) not in CAPTURE_INSTANCE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_factual_content(item) for item in value]
    return value


def _canonical_portfolio_scope(scope: dict[str, Any]) -> Any:
    """The scope canonical form: ``portfolio_ids`` is a set by contract and
    sorts; every other value keeps the generic rule."""

    canonical = dict(_canonical_value(scope))
    portfolio_ids = canonical.get("portfolio_ids")
    if isinstance(portfolio_ids, list) and all(isinstance(item, str) for item in portfolio_ids):
        canonical["portfolio_ids"] = sorted(portfolio_ids)
    return canonical


def _canonical_value(value: Any) -> Any:
    """Deterministic form for nested request values.

    Mappings sort by key; LISTS KEEP THEIR ORDER - a list in a request may
    be semantically ordered (column order, ranking), so a generic sort would
    erase output-affecting semantics. Only fields the series key KNOWS to be
    sets are normalized: ``sections`` and ``allocation_dimensions`` at the
    top level, and ``portfolio_ids`` inside the scope. Scalars pass through
    verbatim.
    """

    if isinstance(value, dict):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _sha256_of(canonical: Any) -> str:
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
