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

from app.report_ordering_catalogue.definitions import REPORT_FAMILY_DEFINITIONS


def resolve_report_template(report_type: str) -> tuple[str, str]:
    """Resolve a report type to its governed (template_id, template_version)."""

    for definition in REPORT_FAMILY_DEFINITIONS:
        if definition.report_type == report_type:
            return definition.template_id, definition.template_version
    raise LookupError(
        f"REPORT_TEMPLATE_UNRESOLVED: no report family definition owns report type "
        f"{report_type!r}; a job without a governed template cannot be accepted for PDF."
    )


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
