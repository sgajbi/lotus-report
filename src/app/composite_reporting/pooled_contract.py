"""Retained pooled result admission; no XIRR, cash-flow or fee calculation."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, FiniteFloat, StrictBool, TypeAdapter, ValidationError, model_validator

from app.composite_reporting.admission import CompositeEvidenceRefused, response_digest
from app.composite_reporting.models import (
    Digest,
    Identifier,
    PooledAnalysisSelection,
    PooledSourcePin,
    SourceModel,
    SourceNumber,
)
from app.composite_reporting.table_contract import TableModel, require_complete_table

_SOURCE_MONEY = TypeAdapter(SourceNumber)


class PooledCashFlow(SourceModel):
    economic_date: date
    amount: SourceNumber
    source_event_ids: list[Identifier]


class PooledObservation(SourceModel):
    tenant_id: Identifier
    composite_id: Identifier
    source_manifest_id: Identifier
    input_manifest_digest: Digest
    period_start: date
    period_end: date
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    opening_value: SourceNumber
    terminal_value: SourceNumber
    investor_cash_flows: list[PooledCashFlow] = Field(max_length=10000)
    portfolio_cash_flows: list[PooledCashFlow] = Field(max_length=10000)
    per_member_controls: dict[str, dict[str, SourceNumber]]
    eliminated_transfer_event_ids: list[Identifier]
    excluded_flow_event_ids: list[Identifier]
    source_bundle: dict[str, Any]


class PooledOutcome(SourceModel):
    availability: Literal["AVAILABLE", "NOT_CALCULABLE", "FALLBACK_ANALYSIS"]
    actual_method: Literal["XIRR", "MODIFIED_DIETZ", "DIETZ"]
    return_value: SourceNumber | None
    annualized_return: SourceNumber | None
    holding_period_return: SourceNumber | None
    units: Literal["DECIMAL_FRACTION"]
    root_precision: Literal["FLOAT64"]
    input_money_precision: Literal["EXACT_DECIMAL"]
    reason_codes: list[Identifier] = Field(max_length=32)
    diagnostics: dict[str, Any]
    original_solver_result: dict[str, Any]


class QualifiedPooledConvergence(SourceModel):
    """Source-stated controls of an AVAILABLE root; no independent solver claim."""

    algorithm: Identifier
    anchor_date: date
    day_count_basis: Literal["BUS/252", "ACT/365", "ACT/ACT"]
    converged: StrictBool
    uniqueness_supported: StrictBool
    non_simple_root_detected: StrictBool
    root_count_detected: int = Field(ge=0, strict=True)
    normalized_flow_count: int = Field(ge=2, strict=True)
    iterations: int = Field(ge=0, strict=True)
    max_iterations: int = Field(ge=1, strict=True)
    root_scan_steps: int = Field(ge=1, strict=True)
    solver_work_units: int = Field(ge=1, strict=True)
    gross_cash_flow_scale: FiniteFloat = Field(gt=0)
    rate_lower_bound: FiniteFloat = Field(gt=-1)
    rate_upper_bound: FiniteFloat
    residual: FiniteFloat
    residual_npv: FiniteFloat
    tolerance: FiniteFloat = Field(gt=0)
    termination_reason: Identifier

    @model_validator(mode="after")
    def require_qualified_control_state(self) -> QualifiedPooledConvergence:
        if not (
            self.converged
            and self.uniqueness_supported
            and not self.non_simple_root_detected
            and self.root_count_detected == 1
            and self.iterations <= self.max_iterations
            and self.rate_upper_bound > self.rate_lower_bound
        ):
            raise ValueError("COMPOSITE_POOLED_XIRR_NOT_QUALIFIED")
        return self


class PooledResponse(SourceModel):
    schema_version: Literal["composite-pooled-mwr.v1"]
    calculation_id: UUID
    metric_id: Literal["POOLED_MONEY_WEIGHTED_RETURN"]
    method: Literal["XIRR:v1"]
    composite_id: Identifier
    input_manifest_digest: Digest
    calculation_engine_version: Identifier
    correction_of_calculation_id: UUID | None
    result_classification: Literal["NON_OFFICIAL_CALCULATED_ANALYSIS"]
    observation: PooledObservation
    outcome: PooledOutcome

    @model_validator(mode="after")
    def require_bound_observation(self) -> PooledResponse:
        if (
            self.composite_id != self.observation.composite_id
            or self.input_manifest_digest != self.observation.input_manifest_digest
            or self.correction_of_calculation_id == self.calculation_id
        ):
            raise ValueError("COMPOSITE_POOLED_OBSERVATION_IDENTITY_CONFLICT")
        return self


def _refuse(condition: bool, code: str) -> None:
    if not condition:
        raise CompositeEvidenceRefused(code)


def _finite_json(value: Any) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise CompositeEvidenceRefused("COMPOSITE_POOLED_NONFINITE_DIAGNOSTIC") from exc


def admit_pooled_response(
    *,
    selection: PooledAnalysisSelection,
    admitted_tenant_id: str,
    status_code: int,
    payload: dict[str, Any],
) -> PooledResponse:
    _refuse(selection.tenant_id == admitted_tenant_id, "COMPOSITE_REPORT_TENANT_MISMATCH")
    _refuse(status_code == 200, "COMPOSITE_REPORT_SOURCE_UNAVAILABLE")
    _finite_json(payload)
    _refuse(
        response_digest(payload) == selection.response_digest, "COMPOSITE_REPORT_RESPONSE_CHANGED"
    )
    source = PooledResponse.model_validate(payload)
    observation = source.observation
    identity = {
        "calculation_id": selection.calculation_id,
        "composite_id": selection.composite_id,
        "schema_version": selection.schema_version,
        "metric_id": selection.metric_id,
        "method": selection.method,
        "input_manifest_digest": selection.input_manifest_digest,
        "calculation_engine_version": selection.engine_version,
        "correction_of_calculation_id": selection.correction_of_calculation_id,
    }
    _refuse(
        all(getattr(source, key) == value for key, value in identity.items()),
        "COMPOSITE_POOLED_SOURCE_IDENTITY_CONFLICT",
    )
    _refuse(
        all(
            getattr(observation, key) == getattr(selection, key)
            for key in (
                "tenant_id",
                "composite_id",
                "source_manifest_id",
                "input_manifest_digest",
                "period_start",
                "period_end",
                "reporting_currency",
            )
        ),
        "COMPOSITE_POOLED_OBSERVATION_IDENTITY_CONFLICT",
    )
    bundle = observation.source_bundle
    _refuse(
        response_digest(bundle) == selection.source_bundle_digest,
        "COMPOSITE_POOLED_BUNDLE_DIGEST_CONFLICT",
    )
    _refuse(
        all(
            bundle.get(key) == getattr(selection, key)
            for key in (
                "tenant_id",
                "composite_id",
                "source_manifest_id",
                "reporting_currency",
            )
        )
        and bundle.get("period_start") == selection.period_start.isoformat()
        and bundle.get("period_end") == selection.period_end.isoformat(),
        "COMPOSITE_POOLED_BUNDLE_SCOPE_CONFLICT",
    )
    _require_bundle(selection, bundle, observation)
    _require_outcome(selection, source.outcome)
    return source


def _require_bundle(
    selection: PooledAnalysisSelection,
    bundle: dict[str, Any],
    observation: PooledObservation,
) -> None:
    _require_population(selection, bundle, observation)
    pin_ids = _require_source_pins(selection, bundle)
    _require_policy(selection, bundle)
    _require_member_money(selection, bundle, pin_ids)
    _require_flow_coverage(selection, bundle, observation, pin_ids)


def _require_population(
    selection: PooledAnalysisSelection, bundle: dict[str, Any], observation: PooledObservation
) -> None:
    expected = selection.expected_portfolio_ids
    _refuse(
        bundle.get("population_complete") is True
        and type(bundle.get("expected_population_count")) is int
        and bundle["expected_population_count"] == len(expected)
        and bundle.get("expected_portfolio_ids") == expected
        and set(observation.per_member_controls) == set(expected),
        "COMPOSITE_POOLED_POPULATION_CONFLICT",
    )
    _refuse(
        bundle.get("qualification") in {"CONTROLLED_SYNTHETIC_ONLY", "OWNER_QUALIFIED_SOURCE"}
        and bundle.get("institutional_attestation") in {"NOT_ATTESTED", "OWNER_ATTESTED"},
        "COMPOSITE_POOLED_QUALIFICATION_REQUIRED",
    )
    _refuse(
        all(
            isinstance(bundle.get(key), str) and bundle[key]
            for key in (
                "definition_revision",
                "definition_hash",
                "membership_revision",
                "membership_hash",
                "compatibility_reference",
                "population_source_pin_id",
            )
        ),
        "COMPOSITE_POOLED_SOURCE_REVISION_REQUIRED",
    )


def _require_source_pins(selection: PooledAnalysisSelection, bundle: dict[str, Any]) -> set[str]:
    pins = [PooledSourcePin.model_validate(pin) for pin in bundle["source_pins"]]
    _refuse(pins == selection.source_pins, "COMPOSITE_POOLED_SOURCE_VECTOR_CONFLICT")
    pin_ids = {pin.pin_id for pin in pins}
    _refuse(
        set(bundle.get("compatible_pin_ids", [])) == pin_ids
        and len(bundle.get("compatible_pin_ids", [])) == len(pin_ids)
        and bundle["population_source_pin_id"] in pin_ids
        and set(bundle["raw_source_bodies"]) == pin_ids,
        "COMPOSITE_POOLED_SOURCE_COMPATIBILITY_CONFLICT",
    )
    for pin in pins:
        _refuse(
            pin.coverage_from <= selection.period_start
            and pin.coverage_to >= selection.period_end
            and response_digest(bundle["raw_source_bodies"][pin.pin_id]) == pin.payload_digest,
            "COMPOSITE_POOLED_SOURCE_BODY_CONFLICT",
        )
    return pin_ids


def _require_policy(selection: PooledAnalysisSelection, bundle: dict[str, Any]) -> None:
    policy = bundle["policy"]
    fields = {
        "binding_id": selection.policy_binding_id,
        "content_hash": selection.policy_content_hash,
        "method": selection.method,
        "return_view": selection.return_view,
        "fee_basis": selection.fee_basis,
        "day_count_basis": selection.day_count_basis,
        "fallback_policy": selection.fallback_policy,
    }
    _refuse(
        all(policy.get(key) == value for key, value in fields.items()),
        "COMPOSITE_POOLED_POLICY_CONFLICT",
    )


def _require_member_money(
    selection: PooledAnalysisSelection, bundle: dict[str, Any], pin_ids: set[str]
) -> None:
    expected = selection.expected_portfolio_ids
    for row in bundle["membership"]:
        _refuse(
            row["portfolio_id"] in expected
            and row["status"] in {"INCLUDED", "EXCLUDED", "PENDING"}
            and date.fromisoformat(row["effective_from"])
            <= date.fromisoformat(row["effective_to"]),
            "COMPOSITE_POOLED_MEMBERSHIP_CONFLICT",
        )
    for row in bundle["valuations"] + bundle["flows"]:
        _refuse(
            row["portfolio_id"] in expected
            and row["currency"] == selection.reporting_currency
            and row["source_pin_id"] in pin_ids
            and row["units"] == "MONETARY_AMOUNT",
            "COMPOSITE_POOLED_MONEY_SCOPE_CONFLICT",
        )
        # Validate exact source money without modifying the retained raw representation.
        _SOURCE_MONEY.validate_python(row["amount"])


def _require_flow_coverage(
    selection: PooledAnalysisSelection,
    bundle: dict[str, Any],
    observation: PooledObservation,
    pin_ids: set[str],
) -> None:
    expected = selection.expected_portfolio_ids
    coverage = bundle["flow_coverage"]
    _refuse(
        len(coverage) == len(expected)
        and {row["portfolio_id"] for row in coverage} == set(expected)
        and all(
            row["complete"] is True
            and row["source_pin_id"] in pin_ids
            and date.fromisoformat(row["coverage_from"]) <= selection.period_start
            and date.fromisoformat(row["coverage_to"]) >= selection.period_end
            for row in coverage
        ),
        "COMPOSITE_POOLED_FLOW_COVERAGE_CONFLICT",
    )
    _refuse(
        all(
            selection.period_start <= row.economic_date <= selection.period_end
            for row in observation.investor_cash_flows + observation.portfolio_cash_flows
        ),
        "COMPOSITE_POOLED_FLOW_DATE_CONFLICT",
    )


def _require_outcome(selection: PooledAnalysisSelection, outcome: PooledOutcome) -> None:
    values = (outcome.return_value, outcome.annualized_return, outcome.holding_period_return)
    diagnostics = outcome.diagnostics
    _refuse(
        diagnostics.get("actual_interval_start") == selection.period_start.isoformat()
        and diagnostics.get("actual_interval_end") == selection.period_end.isoformat()
        and diagnostics.get("day_count_basis", selection.day_count_basis)
        == selection.day_count_basis
        and diagnostics.get("output_units", "DECIMAL_FRACTION") == "DECIMAL_FRACTION",
        "COMPOSITE_POOLED_SOLVER_INTERVAL_CONFLICT",
    )
    if outcome.availability == "NOT_CALCULABLE":
        _refuse(
            all(value is None for value in values) and bool(outcome.reason_codes),
            "COMPOSITE_POOLED_REJECTED_RETURN_PRESENT",
        )
        return
    # Successful and explicitly elected fallback outputs must state their units
    # and date convention. Sparse numerical-domain failures above retain absence.
    _refuse(
        diagnostics.get("day_count_basis") == selection.day_count_basis
        and diagnostics.get("output_units") == "DECIMAL_FRACTION",
        "COMPOSITE_POOLED_SOLVER_INTERVAL_CONFLICT",
    )
    if outcome.availability == "FALLBACK_ANALYSIS":
        _require_elected_fallback(selection, outcome)
    else:
        _require_available_xirr(selection, outcome)


def _require_elected_fallback(selection: PooledAnalysisSelection, outcome: PooledOutcome) -> None:
    _refuse(
        selection.fallback_policy == "ALLOW_MODIFIED_DIETZ"
        and outcome.actual_method == "MODIFIED_DIETZ"
        and outcome.return_value is not None
        and outcome.original_solver_result.get("status") == "FALLBACK_USED"
        and outcome.original_solver_result.get("method") == "MODIFIED_DIETZ"
        and outcome.diagnostics.get("fallback_from") == "XIRR"
        and bool(outcome.diagnostics.get("fallback_reason")),
        "COMPOSITE_POOLED_FALLBACK_NOT_ELECTED",
    )


def _require_available_xirr(selection: PooledAnalysisSelection, outcome: PooledOutcome) -> None:
    convergence = outcome.diagnostics.get("convergence", {})
    values = (outcome.return_value, outcome.annualized_return, outcome.holding_period_return)
    _refuse(
        outcome.actual_method == "XIRR"
        and all(value is not None for value in values)
        and convergence.get("day_count_basis") == selection.day_count_basis
        and outcome.original_solver_result.get("status") == "CALCULATED"
        and outcome.original_solver_result.get("method") == "XIRR",
        "COMPOSITE_POOLED_XIRR_NOT_QUALIFIED",
    )
    try:
        QualifiedPooledConvergence.model_validate(convergence)
    except ValidationError as exc:
        raise CompositeEvidenceRefused("COMPOSITE_POOLED_XIRR_NOT_QUALIFIED") from exc


class PooledColumn(TableModel):
    column_id: Identifier
    label: str
    value_type: Literal["TEXT", "DECIMAL_RETURN", "MONEY"]
    unit: Literal["TEXT", "DECIMAL_RATIO", "CURRENCY_UNITS"]
    display_unit: Literal["TEXT", "PERCENT", "CURRENCY_UNITS"]
    display_conversion: Literal["IDENTITY", "RATIO_TO_PERCENT_DISPLAY"]
    display_decimal_places: int | None = Field(ge=0, le=12)
    display_rounding_mode: Literal["HALF_UP"]
    currency: str | None
    scale: Literal["1"]


class PooledCell(TableModel):
    canonical_value: str | None
    availability: Literal["AVAILABLE", "UNAVAILABLE"]
    reason_codes: list[Identifier] = Field(max_length=32)
    source_pointer: str = Field(
        pattern=r"^/(source_response|selection|report_facts|predecessor_source_response)/"
    )


class PooledRow(TableModel):
    row_id: Identifier
    cells: dict[str, PooledCell]


class PooledTable(TableModel):
    table_id: str = Field(min_length=1, max_length=31)
    title: str
    columns: list[PooledColumn] = Field(min_length=1, max_length=32)
    rows: list[PooledRow] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def complete_rows(self) -> PooledTable:
        require_complete_table(
            [column.column_id for column in self.columns],
            [(row.row_id, set(row.cells)) for row in self.rows],
        )
        return self


class CompositePooledReportData(TableModel):
    contract_version: Literal["composite_review.v5"]
    qualification: Literal["EXPLICIT_RETAINED_CALCULATED_REPLAY"]
    publication_state: Literal["NOT_ATTESTED"]
    tenant_id: Identifier
    selection: PooledAnalysisSelection
    source_response_digest: Digest
    source_response: dict[str, Any]
    predecessor_source_response: dict[str, Any] | None
    report_facts: dict[str, Any]
    tables: list[PooledTable] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def require_complete_projection(self) -> CompositePooledReportData:
        from app.composite_reporting.pooled_tables import pooled_report_facts, pooled_tables

        admit_pooled_response(
            selection=self.selection,
            admitted_tenant_id=self.tenant_id,
            status_code=200,
            payload=self.source_response,
        )
        _refuse(
            self.source_response_digest == self.selection.response_digest,
            "COMPOSITE_REPORT_DATASET_DIGEST_CONFLICT",
        )
        require_predecessor(self.selection, self.predecessor_source_response)
        raw = self.model_dump(mode="json")
        _refuse(
            self.report_facts == pooled_report_facts() and raw["tables"] == pooled_tables(raw),
            "COMPOSITE_POOLED_TABLE_LAYOUT_CONFLICT",
        )
        return self


def require_predecessor(
    selection: PooledAnalysisSelection,
    predecessor: dict[str, Any] | None,
) -> None:
    if selection.correction_of_calculation_id is None:
        _refuse(predecessor is None, "COMPOSITE_POOLED_UNEXPECTED_PREDECESSOR")
        return
    _refuse(predecessor is not None, "COMPOSITE_POOLED_PREDECESSOR_REQUIRED")
    assert predecessor is not None
    _finite_json(predecessor)
    parent = PooledResponse.model_validate(predecessor)
    _refuse(
        response_digest(predecessor) == selection.predecessor_response_digest
        and parent.calculation_id == selection.correction_of_calculation_id
        and parent.composite_id == selection.composite_id
        and parent.observation.tenant_id == selection.tenant_id
        and parent.observation.period_start == selection.period_start
        and parent.observation.period_end == selection.period_end
        and parent.observation.reporting_currency == selection.reporting_currency
        and parent.metric_id == selection.metric_id
        and parent.method == selection.method,
        "COMPOSITE_POOLED_PREDECESSOR_SCOPE_CONFLICT",
    )
    bundle = parent.observation.source_bundle
    _refuse(
        all(
            bundle.get(key) == getattr(parent.observation, key)
            for key in ("tenant_id", "composite_id", "source_manifest_id", "reporting_currency")
        )
        and bundle.get("period_start") == selection.period_start.isoformat()
        and bundle.get("period_end") == selection.period_end.isoformat(),
        "COMPOSITE_POOLED_PREDECESSOR_SCOPE_CONFLICT",
    )
    # The selected raw predecessor digest is the immutable pin. Validate its
    # own population and source bodies without requiring recursive history.
    parent_scope = selection.model_copy(
        update={
            "expected_portfolio_ids": bundle["expected_portfolio_ids"],
            "source_pins": [PooledSourcePin.model_validate(pin) for pin in bundle["source_pins"]],
        }
    )
    _require_bundle(parent_scope, bundle, parent.observation)
    _require_outcome(parent_scope, parent.outcome)


def composite_pooled_report_schema() -> dict[str, Any]:
    schema = CompositePooledReportData.model_json_schema()
    source = PooledResponse.model_json_schema()
    definitions = schema.setdefault("$defs", {})
    definitions.update(source.pop("$defs", {}))
    definitions["PooledResponse"] = source
    schema["properties"]["source_response"] = {"$ref": "#/$defs/PooledResponse"}
    schema["properties"]["predecessor_source_response"] = {
        "anyOf": [{"$ref": "#/$defs/PooledResponse"}, {"type": "null"}]
    }
    return schema
