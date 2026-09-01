from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from app.report_ordering_catalogue.definitions import (
    REPORT_FAMILY_DEFINITIONS,
    ReportFamilyDefinition,
)

_OPERATIONAL_OPTION_KEYS = frozenset({"retention_policy_id", "retain_until_date"})
_TOP_LEVEL_CONFIGURATION_FIELDS = frozenset({"as_of_date", "reporting_currency"})
_MODE_OPTION_KEYS = {
    "explicit_portfolio_batch": frozenset(
        {"batch_manifest_source", "batch_manifest_version", "batch_manifest_hash"}
    )
}


class ReportOrderingSubmissionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def validate_report_ordering_submission(
    *,
    report_family_id: str,
    ordering_mode_id: str,
    requested_output_formats: Sequence[str],
    options: Mapping[str, Any],
    definitions: Sequence[ReportFamilyDefinition] = REPORT_FAMILY_DEFINITIONS,
) -> None:
    definition = _definition(report_family_id, definitions)
    if ordering_mode_id not in {mode.mode_id for mode in definition.ordering_modes}:
        raise ReportOrderingSubmissionError(
            "unsupported_report_ordering_mode",
            "The selected ordering mode is not available for this report family.",
        )
    _validate_output_formats(definition, requested_output_formats)
    _validate_options(definition, ordering_mode_id, options, requested_output_formats)


def _definition(
    report_family_id: str,
    definitions: Sequence[ReportFamilyDefinition],
) -> ReportFamilyDefinition:
    for definition in definitions:
        if definition.report_family_id == report_family_id:
            return definition
    raise ReportOrderingSubmissionError(
        "unknown_report_family",
        "The selected report family is not published by the report ordering catalogue.",
    )


def _validate_output_formats(
    definition: ReportFamilyDefinition,
    requested_output_formats: Sequence[str],
) -> None:
    if not requested_output_formats:
        raise ReportOrderingSubmissionError(
            "report_output_format_required",
            "Select at least one available report output format.",
        )
    if len(requested_output_formats) != len(set(requested_output_formats)):
        raise ReportOrderingSubmissionError(
            "duplicate_report_output_format",
            "Each report output format may be selected only once.",
        )
    if not set(requested_output_formats).issubset(definition.supported_output_formats):
        raise ReportOrderingSubmissionError(
            "unsupported_report_output_format",
            "One or more selected output formats are not available for this report family.",
        )


def _validate_options(
    definition: ReportFamilyDefinition,
    ordering_mode_id: str,
    options: Mapping[str, Any],
    requested_output_formats: Sequence[str],
) -> None:
    configuration_fields = {field.field_id: field for field in definition.configuration_fields}
    allowed_keys = (
        (set(configuration_fields) - _TOP_LEVEL_CONFIGURATION_FIELDS)
        | _OPERATIONAL_OPTION_KEYS
        | _MODE_OPTION_KEYS.get(ordering_mode_id, frozenset())
    )
    if definition.sections:
        allowed_keys.add("sections")
    unknown_keys = sorted(set(options) - allowed_keys)
    if unknown_keys:
        raise ReportOrderingSubmissionError(
            "unsupported_report_configuration",
            "One or more report configuration fields are not available for this report family.",
        )

    if "sections" in options:
        _validate_string_selection(
            value=options["sections"],
            allowed_values={section.section_id for section in definition.sections},
            required_code="report_section_required",
            invalid_code="unsupported_report_section",
            duplicate_code="duplicate_report_section",
            label="report section",
        )
    _validate_section_dependencies(
        definition=definition,
        options=options,
        output_formats=requested_output_formats,
    )
    if "allocation_dimensions" in options:
        field = configuration_fields["allocation_dimensions"]
        _validate_string_selection(
            value=options["allocation_dimensions"],
            allowed_values={option.value for option in field.options},
            required_code="allocation_dimension_required",
            invalid_code="unsupported_allocation_dimension",
            duplicate_code="duplicate_allocation_dimension",
            label="allocation view",
        )
    if "benchmark_code" in options:
        _validate_nonempty_string(options["benchmark_code"], "benchmark")
    if "retention_policy_id" in options:
        _validate_nonempty_string(options["retention_policy_id"], "retention policy")
    if "retain_until_date" in options:
        _validate_business_date(options["retain_until_date"])
    for field_id in ("batch_manifest_source", "batch_manifest_version", "batch_manifest_hash"):
        if field_id in options:
            _validate_nonempty_string(options[field_id], "batch manifest provenance")


def _validate_section_dependencies(
    *,
    definition: ReportFamilyDefinition,
    options: Mapping[str, Any],
    output_formats: Sequence[str],
) -> None:
    """Selecting a section makes each of its `conditional` configuration
    fields required - acceptance must refuse what dispatch would refuse, or a
    durably accepted batch strands its items at materialization."""

    raw_sections = options.get("sections")
    selected = (
        {item.upper() for item in raw_sections if isinstance(item, str)}
        if isinstance(raw_sections, list)
        else set()
    )
    if not selected:
        return
    configuration_fields = {field.field_id: field for field in definition.configuration_fields}
    for section in definition.sections:
        if section.section_id not in selected:
            continue
        for dependency_id in section.dependency_field_ids:
            field = configuration_fields.get(dependency_id)
            if field is None or field.requirement != "conditional":
                continue
            value = options.get(dependency_id)
            if not isinstance(value, str) or not value.strip():
                raise ReportOrderingSubmissionError(
                    "missing_conditional_report_field",
                    f"The {section.business_label} section requires the "
                    f"{field.business_label} field.",
                )


def _validate_string_selection(
    *,
    value: Any,
    allowed_values: set[str],
    required_code: str,
    invalid_code: str,
    duplicate_code: str,
    label: str,
) -> None:
    if not isinstance(value, list) or not value:
        raise ReportOrderingSubmissionError(
            required_code,
            f"Select at least one {label}.",
        )
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ReportOrderingSubmissionError(
            invalid_code,
            f"One or more selected {label} values are invalid.",
        )
    if len(value) != len(set(value)):
        raise ReportOrderingSubmissionError(
            duplicate_code,
            f"Each {label} may be selected only once.",
        )
    if not set(value).issubset(allowed_values):
        raise ReportOrderingSubmissionError(
            invalid_code,
            f"One or more selected {label} values are not available.",
        )


def _validate_nonempty_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ReportOrderingSubmissionError(
            "invalid_report_configuration",
            f"The selected {label} value is invalid.",
        )


def _validate_business_date(value: Any) -> None:
    if not isinstance(value, str):
        raise ReportOrderingSubmissionError(
            "invalid_retain_until_date",
            "The retain-until date must be a valid business date.",
        )
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ReportOrderingSubmissionError(
            "invalid_retain_until_date",
            "The retain-until date must be a valid business date.",
        ) from exc
