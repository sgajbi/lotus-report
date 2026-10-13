"""Concrete frozen Archive v8 custody type, consumed without runtime Archive imports."""

import json
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import Field, model_serializer, model_validator

from app.composite_reporting.correction_notice.selection import NoticeModel
from app.composite_reporting.models import (
    CompositeDefinitionPin,
    CompositeReportSelection,
    CompositeSourceContextSelection,
    Digest,
    ReturnProductSelection,
    require_product_scope,
)

SCHEMA_ROOT = Path(__file__).parent / "inherited_schemas"


def inherited_schema(name: str) -> dict[str, Any]:
    schema = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise ValueError("COMPOSITE_NOTICE_SCHEMA_OBJECT_REQUIRED")
    return schema


def embedded_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline a foreign schema's local references before Pydantic composes it.

    The inherited contracts have no recursive types. Keeping their local $defs
    references would bind them to the containing notice's unrelated definitions.
    Refuse a recursive or external reference rather than weakening its contract.
    """
    definitions = schema.get("$defs", {})

    def expand(value: Any, active: tuple[str, ...] = ()) -> Any:
        if isinstance(value, list):
            return [expand(item, active) for item in value]
        if not isinstance(value, dict):
            return value
        result = {
            key: expand(item, active) for key, item in value.items() if key not in ("$defs", "$ref")
        }
        if "$ref" in value:
            reference = value["$ref"]
            name = reference.removeprefix("#/$defs/")
            if reference != "#/$defs/" + name or name in active or name not in definitions:
                raise ValueError("COMPOSITE_NOTICE_SCHEMA_REFERENCE_UNSUPPORTED")
            target = expand(definitions[name], (*active, name))
            return {"allOf": [target, result]} if result else target
        return result

    result = expand(schema)
    if not isinstance(result, dict):
        raise ValueError("COMPOSITE_NOTICE_SCHEMA_OBJECT_REQUIRED")
    return result


def validate_inherited(name: str, value: Any) -> None:
    if not Draft202012Validator(inherited_schema(name)).is_valid(value):
        raise ValueError("COMPOSITE_NOTICE_INHERITED_CONTRACT_INVALID")


class CompositeSourceProductCustody(NoticeModel):
    pin: ReturnProductSelection
    source_response_digest: Digest

    @model_validator(mode="after")
    def require_digest(self) -> "CompositeSourceProductCustody":
        if self.source_response_digest != self.pin.selection.response_digest:
            raise ValueError("COMPOSITE_NOTICE_SOURCE_PRODUCT_DIGEST")
        return self


class CompositeDefinitionCustody(NoticeModel):
    pin: CompositeDefinitionPin
    source_response_digest: Digest

    @model_validator(mode="after")
    def require_digest(self) -> "CompositeDefinitionCustody":
        if self.source_response_digest != self.pin.response_digest:
            raise ValueError("COMPOSITE_NOTICE_DEFINITION_DIGEST")
        return self


class CompositeReportIdentityV8(NoticeModel):
    contract_version: Literal["composite_review.v8"]
    qualification: Literal["EXPLICIT_RETAINED_CALCULATED_REPLAY"]
    publication_state: Literal["NOT_ATTESTED"]
    selection: CompositeReportSelection
    series_digest: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    source_revision_digest: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    factual_content_digest: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    source_context: CompositeSourceContextSelection
    definition: CompositeDefinitionCustody
    source_products: list[CompositeSourceProductCustody] = Field(default_factory=list, max_length=8)

    @model_serializer(mode="wrap")
    def preserve_optional_products(self, handler: Any) -> dict[str, Any]:
        result: dict[str, Any] = handler(self)
        if not self.source_products:
            result.pop("source_products", None)
        return result

    @model_validator(mode="after")
    def require_frozen_custody(self) -> "CompositeReportIdentityV8":
        validate_inherited("archive-identity.v8.schema.json", self.model_dump(mode="json"))
        if self.source_products:
            require_product_scope(self.selection, [product.pin for product in self.source_products])
        if self.source_context.definition != self.definition.pin or any(
            window.definition_content_hash != self.definition.pin.content_hash
            for window in self.selection.windows
        ):
            raise ValueError("COMPOSITE_NOTICE_SOURCE_DEFINITION_CONFLICT")
        if self.source_context.memberships and not {
            window.membership_content_hash for window in self.selection.windows
        }.issubset({pin.content_hash for pin in self.source_context.memberships}):
            raise ValueError("COMPOSITE_NOTICE_SOURCE_MEMBERSHIP_CONFLICT")
        return self
