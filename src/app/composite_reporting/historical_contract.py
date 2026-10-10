"""Additive v7 normalized policy custody; frozen monthly products are unchanged."""

from typing import Any, Literal

from pydantic import Field, model_validator

from app.composite_reporting.eligibility_contract import (
    EligibilityTable,
    EvaluatedSourceMonth,
    PublishedSourceMonth,
)
from app.composite_reporting.models import HistoricalEligibilitySelection
from app.composite_reporting.table_contract import TableModel


class HistoricalEvaluatedMonth(EvaluatedSourceMonth):
    lineage_receipts: list[dict[str, Any]] = Field(default_factory=list, max_length=31)


class HistoricalPublishedMonth(PublishedSourceMonth):
    lineage_receipts: list[dict[str, Any]] = Field(default_factory=list, max_length=31)
    parent_publication: dict[str, Any] | None = None


class CompositeHistoricalReportData(TableModel):
    contract_version: Literal["composite_review.v7"]
    qualification: Literal["CONTROLLED_HISTORICAL_POLICY_EVIDENCE_REPLAY"]
    publication_state: Literal["NOT_ATTESTED"]
    tenant_id: str
    selection: HistoricalEligibilitySelection
    source_months: list[HistoricalEvaluatedMonth | HistoricalPublishedMonth] = Field(
        min_length=1, max_length=120
    )
    report_facts: dict[str, Any]
    tables: list[EligibilityTable] = Field(min_length=10, max_length=10)

    @model_validator(mode="after")
    def require_complete_custody(self) -> "CompositeHistoricalReportData":
        from app.composite_reporting.historical_admission import require_months
        from app.composite_reporting.historical_tables import historical_facts, historical_tables

        raw = self.model_dump(mode="json")
        require_months(self.selection, raw["source_months"])
        if (
            self.tenant_id != self.selection.tenant_id
            or self.report_facts != historical_facts()
            or raw["tables"] != historical_tables(raw)
        ):
            raise ValueError(
                "Historical policy projection must preserve every declared source cell"
            )
        return self


def composite_historical_report_schema() -> dict[str, Any]:
    """Publish the exact producer schemas with isolated definitions and references."""
    from app.composite_reporting.eligibility_contract import composite_eligibility_report_schema
    from app.composite_reporting.historical_source import product_schema

    schema = CompositeHistoricalReportData.model_json_schema()
    frozen = composite_eligibility_report_schema()["$defs"]
    schema["$defs"].update(frozen)

    def namespace(value: Any, key: str) -> Any:
        if isinstance(value, dict):
            return {name: namespace(child, key) for name, child in value.items()}
        if isinstance(value, list):
            return [namespace(child, key) for child in value]
        if isinstance(value, str) and value.startswith("#/$defs/"):
            return f"#/$defs/{key}/$defs/" + value.removeprefix("#/$defs/")
        return value

    references: dict[str, dict[str, Any]] = {}
    for name in (
        "CompositeMonthlyEvaluationProposal",
        "CompositeMonthlyEligibilityPublicationReceipt",
    ):
        choices = []
        for version in ("v3", "v4"):
            key = name + "_" + version
            schema["$defs"][key] = namespace(product_schema(name, version), key)
            choices.append({"$ref": f"#/$defs/{key}"})
        references[name] = {"oneOf": choices}
    for variant in ("HistoricalEvaluatedMonth", "HistoricalPublishedMonth"):
        properties = schema["$defs"][variant]["properties"]
        properties["lineage_receipts"]["items"] = references[
            "CompositeMonthlyEligibilityPublicationReceipt"
        ]
        if variant == "HistoricalEvaluatedMonth":
            properties["proposal"] = references["CompositeMonthlyEvaluationProposal"]
        else:
            properties["receipt"] = references["CompositeMonthlyEligibilityPublicationReceipt"]
            for key in ("membership", "universe", "parent_membership", "publication"):
                properties[key] = frozen["PublishedSourceMonth"]["properties"][key]
            properties["parent_publication"] = {
                "anyOf": [{"$ref": "#/$defs/EligibilityPublication"}, {"type": "null"}]
            }
    return schema
