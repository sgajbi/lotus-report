"""The one authority for family template selection.

``REPORT_FAMILY_DEFINITIONS`` already states, per report family, the template
id and version Report intends to order. This resolver is the single internal
path from a report type to that governed pair - the render envelope never
invents a template version, and no second template registry exists.

The resolved pair is persisted on the report job at ACCEPTANCE for
PDF-capable jobs: template selection is an immutable job fact. A deployment
that changes a family's default template version changes only jobs accepted
after it; every existing job renders, recovers, and rerenders on the exact
presentation contract it was accepted under.
"""

from __future__ import annotations

from collections.abc import Collection

from app.report_ordering_catalogue.definitions import (
    REPORT_FAMILY_DEFINITIONS,
    ReportFamilyDefinition,
)


def resolve_report_family(report_type: str) -> ReportFamilyDefinition:
    """Resolve a report type to its governing ReportFamilyDefinition."""

    for definition in REPORT_FAMILY_DEFINITIONS:
        if definition.report_type == report_type:
            return definition
    raise LookupError(
        f"REPORT_TEMPLATE_UNRESOLVED: no report family definition owns report type "
        f"{report_type!r}; a job without a governed template cannot be accepted for PDF."
    )


def resolve_report_template(report_type: str) -> tuple[str, str]:
    """Resolve a report type to its governed (template_id, template_version)."""

    definition = resolve_report_family(report_type)
    return definition.template_id, definition.template_version


def resolve_report_data_contract(report_type: str) -> str:
    """Resolve a report type to the report_data contract version it emits."""

    return resolve_report_family(report_type).report_data_contract_version


def accepted_template_identity(
    report_type: str, output_formats: Collection[str] | None
) -> tuple[str | None, str | None]:
    """The template pair to persist at job acceptance.

    Only a PDF-capable job binds a presentation contract; a JSON-only job
    carries none.
    """

    if "pdf" not in (output_formats or ()):
        return None, None
    return resolve_report_template(report_type)


def job_template_identity(
    report_type: str,
    output_formats: Collection[str] | None,
    inherited: tuple[str | None, str | None] | None = None,
) -> tuple[str | None, str | None]:
    """The template pair a job is accepted under.

    A replay RECOVERS an accepted job and inherits the source job's exact
    pair - a deployment that moved the family default between acceptance and
    replay must not reinterpret the document. Every other acceptance
    resolves the current governed default.
    """

    if inherited is not None:
        return inherited
    return accepted_template_identity(report_type, output_formats)


#: The record's own schema tag, persisted with every contract so a future
#: axis change is distinguishable from history.
ACCEPTED_DOCUMENT_CONTRACT_VERSION = "adc.v1"

#: Governed presentation constants shared by every family today. They live
#: HERE - the acceptance-time authority - so the render envelope consumes
#: the accepted value instead of restating its own.
GOVERNED_LOCALE = "en-SG"
GOVERNED_BRAND_VARIANT = "private_banking"
GOVERNED_RENDER_PACKAGE_VERSION = "render_package.v1"


def accepted_document_contract(
    report_type: str,
    output_formats: Collection[str] | None,
    *,
    input_snapshot_contract_version: str,
    inherited_template: tuple[str | None, str | None] | None = None,
) -> dict[str, str | None]:
    """EVERY contract axis a job is accepted under, resolved ONCE at
    acceptance and persisted with the job (report#283, audit finding 6).

    No lifecycle path may reinterpret an accepted job against today's
    definitions: capture reads the input-snapshot contract from here, the
    render envelope reads the report-data contract, template pair,
    envelope version, locale, brand, and disclosure baseline from here,
    and a replay inherits the source job's persisted contract verbatim.
    Regeneration is a fresh capture and resolves the then-current contract.
    """

    definition = resolve_report_family(report_type)
    template_id, template_version = job_template_identity(
        report_type, output_formats, inherited_template
    )
    return {
        "accepted_contract_version": ACCEPTED_DOCUMENT_CONTRACT_VERSION,
        "report_family_id": definition.report_family_id,
        "report_type": definition.report_type,
        # The DPM families own fixed bounded-input schemas; portfolio
        # review captures under the runtime reporting contract passed in.
        "input_snapshot_contract_version": (
            definition.input_snapshot_contract_version or input_snapshot_contract_version
        ),
        "report_data_contract_version": definition.report_data_contract_version,
        "render_package_version": GOVERNED_RENDER_PACKAGE_VERSION,
        "template_id": template_id,
        "template_version": template_version,
        "locale": GOVERNED_LOCALE,
        "brand_variant": GOVERNED_BRAND_VARIANT,
        "standard_disclosure_ref": definition.standard_disclosure_ref,
    }
