"""Controlled literal SI example; no live producer/full-SI acceptance claim."""

from app.composite_reporting.admission import response_digest
from app.composite_reporting.eligibility_admission import source_hash
from app.composite_reporting.models import CompositeDefinitionPin
from app.composite_reporting.source_context_contract import (
    CapturedInceptionDefinition,
    definition_endpoint,
)
from tests.unit.composite_reporting.fixtures import calculated_example, selection_for


def inception_example(inception="2026-01-01", version="d1"):
    definition = {
        "product_name": "CompositeDefinition",
        "product_version": "v1",
        "tenant_id": "tenant-a",
        "composite_id": "NEUTRAL_BALANCED",
        "definition_version": version,
        "display_name": "Controlled inception example",
        "reporting_currency": "USD",
        "inception_date": inception,
        "termination_date": None,
        "calculation_method": "ASSET_WEIGHTED",
        "source_authority": {"policy_version": "controlled-v1"},
        "created_at": "2026-10-11T00:00:00Z",
    }
    definition["content_hash"] = source_hash(definition, recursive=True)
    source = calculated_example()
    for window in source["selection_manifest"]["windows"]:
        window["definition_content_hash"] = definition["content_hash"]
    selection = selection_for(source)
    pin = CompositeDefinitionPin(
        definition_version=version,
        content_hash=definition["content_hash"],
        response_digest=response_digest(definition),
    )
    captured = CapturedInceptionDefinition(
        pin=pin,
        endpoint=definition_endpoint(selection, pin),
        method="GET",
        source_response_digest=pin.response_digest,
        source_response=definition,
    )
    return selection, source, captured
