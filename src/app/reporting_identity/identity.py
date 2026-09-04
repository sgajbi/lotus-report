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
  source revision vector digest, and the immutable snapshot hash.

Invariants (each pinned by tests):

  same revision id -> same tenant, same semantic request, same source
  revision vector, same immutable snapshot facts;
  a restatement or backdated correction that changes source facts ->
  a different report revision;
  a pure rerender of the same snapshot -> the SAME report revision.

The structured fields remain authoritative everywhere; the opaque id is a
stable reference, never a string to parse.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

REPORT_REVISION_ID_VERSION = "rrv2"

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


class ReportSeriesKey(BaseModel):
    """The logical requested report: who ordered what, about which scope,
    for which business date, with which output-affecting options."""

    model_config = ConfigDict(frozen=True)

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

    def canonical(self) -> dict[str, Any]:
        """Deterministic canonical form: order-insensitive collections are
        sorted, absent optionals are omitted, values stay verbatim."""

        canonical: dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "report_family_id": self.report_family_id,
            "report_type": self.report_type,
            "portfolio_scope": _canonical_value(self.portfolio_scope),
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
        return {
            name: value
            for name in _SOURCE_REVISION_FIELDS
            if (value := getattr(self, name)) is not None
        }


class SourceRevisionVector(BaseModel):
    """The per-source revision evidence behind one captured report.

    ``coverage`` states honestly how much of the vector is evidence-backed:
    ``complete`` only when every participating source stated revision
    evidence; otherwise ``partial`` or ``unknown``. Nothing upgrades it.
    """

    model_config = ConfigDict(frozen=True)

    revisions: tuple[SourceRevision, ...] = ()
    coverage: str = "unknown"

    def canonical(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage,
            "revisions": sorted(
                (revision.canonical() for revision in self.revisions),
                key=lambda item: (
                    str(item.get("source_service", "")),
                    str(item.get("source_product", "")),
                    str(item.get("content_hash", "")),
                    str(item.get("calculation_run_id", "")),
                ),
            ),
        }

    def digest(self) -> str:
        return _sha256_of(self.canonical())


class ReportRevisionIdentity(BaseModel):
    """The restatement-safe identity of one captured report revision."""

    model_config = ConfigDict(frozen=True)

    report_revision_id: str
    series_digest: str
    source_revision_digest: str
    snapshot_hash: str


def derive_report_revision(
    *,
    series_key: ReportSeriesKey,
    source_revisions: SourceRevisionVector,
    snapshot_hash: str,
) -> ReportRevisionIdentity:
    """Derive the deterministic report-revision identity.

    The id changes when - and only when - the semantic request, the source
    revision evidence, or the immutable snapshot facts change. Capture time
    deliberately does NOT participate: two captures that produced identical
    facts from identical source revisions ARE the same revision, and a pure
    rerender (same snapshot) never mints a new one.
    """

    if not snapshot_hash.strip():
        raise ValueError(
            "REPORT_REVISION_SNAPSHOT_HASH_REQUIRED: a report revision exists "
            "only for captured facts; refusing to mint identity without the "
            "immutable snapshot hash."
        )
    canonical = {
        "identity_version": REPORT_REVISION_ID_VERSION,
        "series": series_key.canonical(),
        "source_revisions": source_revisions.canonical(),
        "snapshot_hash": snapshot_hash,
    }
    return ReportRevisionIdentity(
        report_revision_id=f"{REPORT_REVISION_ID_VERSION}_{_sha256_of(canonical)}",
        series_digest=series_key.digest(),
        source_revision_digest=source_revisions.digest(),
        snapshot_hash=snapshot_hash,
    )


def _canonical_value(value: Any) -> Any:
    """Deterministic form for nested request values.

    Mappings sort by key; LISTS KEEP THEIR ORDER (a list in a request may be
    semantically ordered - only fields the series key knows to be sets, like
    sections, are sorted, and portfolio_ids below is normalized explicitly);
    scalars pass through verbatim.
    """

    if isinstance(value, dict):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        items = [_canonical_value(item) for item in value]
        if all(isinstance(item, str) for item in items):
            return sorted(items)
        return items
    return value


def _sha256_of(canonical: Any) -> str:
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
