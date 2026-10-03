"""Preserve allocation uncertainty; never calculate source financial values."""

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from app.application_errors import ReportingUpstreamError
from app.models.allocation_qualification import AllocationQualification, AllocationValuationCoverage
from app.reporting_lineage.allocation_presentation import ALLOCATION_DIMENSIONS

DIMENSION_KEYS = dict(ALLOCATION_DIMENSIONS)
QUALIFICATION_TEXT = {
    "allocation_valuation_complete": "Allocation uses source-qualified valuation coverage.",
    "allocation_valuation_partial": (
        "Allocation valuation coverage is incomplete; unavailable values and weights "
        "remain unavailable."
    ),
    "allocation_valuation_carry_forward": (
        "Allocation uses source-qualified carried-forward valuations."
    ),
    "allocation_valuation_missing": (
        "Allocation valuation qualification is unavailable; completeness is not attested."
    ),
    "allocation_valuation_invalid": (
        "Allocation valuation evidence is inconsistent; completeness is not attested."
    ),
    "allocation_numeric_unavailable": (
        "One or more allocation values or weights are unavailable; "
        "no replacement zero was calculated."
    ),
}


def allocation_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        result = float(number)
    except (InvalidOperation, ValueError, OverflowError):
        return None
    if not number.is_finite() or not Decimal(str(result)).is_finite():
        return None
    return None if number != 0 and result == 0 else result


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bounded(value: object) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= 128 else None


def _iso_date(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _currency(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 3
        and value.isascii()
        and value.isalpha()
        and value.isupper()
    )


def _source_binding(payload: dict[str, Any], request: dict[str, Any]) -> bool:
    scope = _dict(payload.get("scope"))
    wanted = _dict(request.get("scope")).get("portfolio_id")
    comparisons = (
        (payload.get("scope_type"), "portfolio"),
        (scope.get("portfolio_id"), wanted),
        (payload.get("resolved_as_of_date"), request.get("as_of_date")),
        (payload.get("reporting_currency"), request.get("reporting_currency")),
    )
    for actual, expected in comparisons:
        if actual is not None and expected is not None and actual != expected:
            raise ReportingUpstreamError("lotus-core allocation source scope mismatch.")
    if scope.get("portfolio_ids") or scope.get("booking_center_code"):
        raise ReportingUpstreamError("lotus-core allocation source scope mismatch.")
    currency = payload.get("reporting_currency")
    return bool(
        wanted
        and scope.get("portfolio_id") == wanted
        and payload.get("scope_type") == "portfolio"
        and payload.get("resolved_as_of_date") == request.get("as_of_date")
        and _iso_date(payload.get("resolved_as_of_date"))
        and _currency(currency)
    )


def map_source_allocation(payload: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    bound = _source_binding(payload, request)
    if payload.get("valuation_coverage") is not None and not bound:
        raise ReportingUpstreamError("lotus-core allocation source binding missing.")
    allocation: dict[str, Any] = {"view_totals": {}}
    malformed = not isinstance(payload.get("views"), list)
    for view in payload.get("views", []) if not malformed else []:
        raw = _dict(view)
        dimension = raw.get("dimension")
        key = DIMENSION_KEYS.get(dimension) if isinstance(dimension, str) else None
        if key is None or key in allocation or not isinstance(raw.get("buckets"), list):
            malformed = True
            continue
        buckets, invalid_buckets = _map_source_buckets(
            raw["buckets"], portfolio_id=_dict(request.get("scope")).get("portfolio_id")
        )
        allocation[key] = buckets
        allocation["view_totals"][key] = allocation_number(
            raw.get("total_market_value_reporting_currency")
        )
        malformed |= invalid_buckets
    requested = request.get("dimensions")
    if isinstance(requested, list):
        malformed |= any(DIMENSION_KEYS.get(str(item)) not in allocation for item in requested)
    malformed |= _contradictory_source_numbers(payload, allocation)
    qualification = _qualify(payload, allocation, malformed=malformed)
    if qualification["status"] == "invalid":
        _suppress_weights(allocation)
    for field in ("look_through", "calculation_lineage"):
        if field in payload:
            allocation[field] = deepcopy(payload[field])
    allocation["total_market_value_reporting_currency"] = allocation_number(
        payload.get("total_market_value_reporting_currency")
    )
    allocation["valuation_coverage"] = qualification["coverage"]
    allocation["qualification"] = qualification
    allocation["supportability"] = {
        "status": "ready" if qualification["client_publication_allowed"] else "partial",
        "reason_code": qualification["reason_code"],
        "message": allocation_statement(qualification),
    }
    return allocation


def _map_source_buckets(
    source: list[object], *, portfolio_id: str | None
) -> tuple[list[dict[str, Any]], bool]:
    buckets: list[dict[str, Any]] = []
    malformed = False
    for value in source:
        bucket = _dict(value)
        label = bucket.get("dimension_value")
        if not isinstance(label, str) or not label.strip():
            malformed = True
            continue
        row = {
            "group": label,
            "weight": allocation_number(bucket.get("weight")),
            "market_value": allocation_number(bucket.get("market_value_reporting_currency")),
            "position_count": bucket.get("position_count"),
        }
        for field in (
            "contributors",
            "contributor_count",
            "omitted_contributor_count",
            "omitted_market_value_reporting_currency",
        ):
            if field in bucket:
                row[field] = deepcopy(bucket[field])
        malformed |= _qualify_contributors(row, portfolio_id=portfolio_id)
        buckets.append(row)
        malformed |= any(
            bucket.get(field) is not None and allocation_number(bucket[field]) is None
            for field in ("weight", "market_value_reporting_currency")
        )
    return buckets, malformed


def _qualify_contributors(row: dict[str, Any], *, portfolio_id: str | None) -> bool:
    malformed = _admit_bucket_counts(row)
    contributors = row.get("contributors", [])
    if not isinstance(contributors, list):
        row["contributors"] = []
        return True
    for contributor in contributors:
        if not isinstance(contributor, dict):
            malformed = True
            continue
        if (
            contributor.get("portfolio_id") is not None
            and contributor["portfolio_id"] != portfolio_id
        ):
            raise ReportingUpstreamError("lotus-core allocation contributor scope mismatch.")
        for field in ("market_value_reporting_currency", "bucket_weight", "component_weight"):
            raw = contributor.get(field)
            if raw is not None and allocation_number(raw) is None:
                malformed = True
                contributor[field] = None
    if "contributors" in row:
        row["contributors"] = [item for item in contributors if isinstance(item, dict)]
    residual = row.get("omitted_market_value_reporting_currency")
    if residual is not None and allocation_number(residual) is None:
        malformed = True
        row["omitted_market_value_reporting_currency"] = None
    return malformed


def _admit_bucket_counts(row: dict[str, Any]) -> bool:
    malformed = False
    for field in ("position_count", "contributor_count", "omitted_contributor_count"):
        if field not in row:
            continue
        count = row[field]
        if type(count) is not int or count < 0:
            row[field] = None
            malformed = True
    contributors = row.get("contributors")
    total, omitted = row.get("contributor_count"), row.get("omitted_contributor_count")
    if isinstance(contributors, list) and type(total) is int:
        malformed |= (
            total != len(contributors) + omitted
            if type(omitted) is int
            else total < len(contributors)
        )
    return malformed


def _qualify(
    payload: dict[str, Any], allocation: dict[str, Any], *, malformed: bool
) -> dict[str, Any]:
    result = AllocationQualification(status="missing", reason_code="allocation_valuation_missing")
    result.source_portfolio_id = _bounded(_dict(payload.get("scope")).get("portfolio_id"))
    source_date = payload.get("resolved_as_of_date")
    source_currency = payload.get("reporting_currency")
    result.source_as_of_date = source_date if _iso_date(source_date) else None
    result.source_reporting_currency = source_currency if _currency(source_currency) else None
    try:
        result.coverage = AllocationValuationCoverage.model_validate(
            payload.get("valuation_coverage")
        )
    except ValidationError:
        if "valuation_coverage" in payload:
            result.status, result.reason_code = "invalid", "allocation_valuation_invalid"
        return result.model_dump(mode="json")
    state = result.coverage.coverage_state
    if malformed:
        result.status, result.reason_code = "invalid", "allocation_valuation_invalid"
    elif state in {"PARTIAL", "UNAVAILABLE", "CARRY_FORWARD"}:
        result.status = "partial"
        result.reason_code = (
            "allocation_valuation_carry_forward"
            if state == "CARRY_FORWARD"
            else "allocation_valuation_partial"
        )
    elif any(
        row.get("weight") is None or row.get("market_value") is None
        for key in DIMENSION_KEYS.values()
        for row in allocation.get(key, [])
    ):
        result.status, result.reason_code = "partial", "allocation_numeric_unavailable"
    else:
        result.status, result.reason_code = "complete", "allocation_valuation_complete"
        result.client_publication_allowed = True
    return result.model_dump(mode="json")


def _contradictory_source_numbers(payload: dict[str, Any], allocation: dict[str, Any]) -> bool:
    state = _dict(payload.get("valuation_coverage")).get("coverage_state")
    total = payload.get("total_market_value_reporting_currency")
    view_totals = allocation.get("view_totals", {})
    if not isinstance(view_totals, dict):
        return True
    rows = [row for key in DIMENSION_KEYS.values() for row in allocation.get(key, [])]
    totals = [total, *view_totals.values()]
    if state in {"PARTIAL", "UNAVAILABLE"}:
        return any(value is not None for value in totals) or any(
            row["weight"] is not None for row in rows
        )
    if state in {"COMPLETE", "MEASURED_ZERO", "CARRY_FORWARD", "LOADED_EMPTY"}:
        return _contradictory_covered_numbers(state, allocation, totals, rows)
    return False


def _contradictory_covered_numbers(
    state: str, allocation: dict[str, Any], totals: list[object], rows: list[dict[str, Any]]
) -> bool:
    views = [allocation[key] for key in DIMENSION_KEYS.values() if key in allocation]
    if not views or any(bool(view) == (state == "LOADED_EMPTY") for view in views):
        return True
    if any(allocation_number(value) is None for value in totals):
        return True
    if state in {"MEASURED_ZERO", "LOADED_EMPTY"}:
        return any(allocation_number(value) != 0 for value in totals) or any(
            row["market_value"] != 0 for row in rows
        )
    return any(row["market_value"] is None for row in rows)


def allocation_statement(qualification: dict[str, Any]) -> str:
    return QUALIFICATION_TEXT.get(
        str(qualification.get("reason_code")), QUALIFICATION_TEXT["allocation_valuation_invalid"]
    )


def allocation_items(allocation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "item_type": "allocation_bucket",
            "view": key,
            "rank": rank,
            "group": row.get("group"),
            "weight": allocation_number(row.get("weight")),
            "market_value": allocation_number(row.get("market_value")),
            "position_count": row.get("position_count"),
        }
        for key in DIMENSION_KEYS.values()
        for rank, row in enumerate(
            allocation.get(key, []) if isinstance(allocation.get(key), list) else [], start=1
        )
        if isinstance(row, dict)
    ]


def top_allocation_bucket(buckets: list[object]) -> dict[str, Any] | None:
    # An unknown competitor prevents an honest claim of the largest bucket.
    ranked = []
    for row in buckets:
        if not isinstance(row, dict):
            return None
        numeric = allocation_number(row.get("weight"))
        if numeric is None:
            return None
        ranked.append((numeric, row))
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def allocation_key_figure(bucket: dict[str, Any] | None) -> dict[str, Any] | None:
    if bucket is None:
        return None
    weight = allocation_number(bucket.get("weight"))
    return {
        "name": bucket.get("group"),
        "weight_pct": None if weight is None else allocation_number(Decimal(str(weight)) * 100),
        "market_value_reporting_currency": allocation_number(bucket.get("market_value")),
        "position_count": bucket.get("position_count"),
    }


def allocation_requested(options: dict[str, Any]) -> bool:
    sections = options.get("sections")
    if not isinstance(sections, list):
        return True
    selected = {item.upper() for item in sections if isinstance(item, str)}
    return not selected or "ALLOCATION" in selected


def snapshot_allocation(snapshot: dict[str, Any], *, requested: bool = False) -> dict[str, Any]:
    """Conservative presentation copy; immutable capture bytes remain untouched."""
    raw = snapshot.get("allocation")
    original = _dict(raw)
    if not original:
        if not requested and "allocation" not in snapshot:
            return {}
        malformed = raw is not None and not isinstance(raw, dict)
        result = AllocationQualification(
            status="invalid" if malformed else "missing",
            reason_code="allocation_valuation_invalid"
            if malformed
            else "allocation_valuation_missing",
        )
        return {"qualification": result.model_dump(mode="json")}
    allocation = deepcopy(original)
    result, suppress = _captured_qualification(snapshot, allocation)
    malformed, unknown = _present_snapshot_buckets(
        allocation, suppress=suppress, portfolio_id=_bounded(snapshot.get("portfolio_id"))
    )
    malformed |= _contradictory_source_numbers(
        {
            "valuation_coverage": allocation.get("valuation_coverage"),
            "total_market_value_reporting_currency": allocation.get(
                "total_market_value_reporting_currency"
            ),
        },
        allocation,
    )
    if malformed and not suppress:
        result.status, result.reason_code = "invalid", "allocation_valuation_invalid"
    if result.status == "invalid":
        _suppress_weights(allocation)
    result.client_publication_allowed &= (
        result.status == "complete"
        and result.coverage is not None
        and result.coverage.coverage_state in {"COMPLETE", "MEASURED_ZERO", "LOADED_EMPTY"}
    )
    if unknown:
        result.client_publication_allowed = False
        if result.status == "complete":
            result.status, result.reason_code = "partial", "allocation_numeric_unavailable"
    allocation["supportability"] = {
        "status": "ready" if result.client_publication_allowed else "partial",
        "reason_code": result.reason_code,
        "message": allocation_statement(result.model_dump(mode="json")),
    }
    allocation["qualification"] = result.model_dump(mode="json")
    return allocation


def _captured_qualification(
    snapshot: dict[str, Any], allocation: dict[str, Any]
) -> tuple[AllocationQualification, bool]:
    try:
        result = AllocationQualification.model_validate(allocation.get("qualification"))
        date.fromisoformat(result.source_as_of_date or "")
        bound = (
            result.source_portfolio_id == snapshot.get("portfolio_id")
            and bool(result.source_portfolio_id)
            and result.source_as_of_date == snapshot.get("as_of_date")
            and result.source_reporting_currency == snapshot.get("reportingCurrency")
        )
        if not bound or (
            result.coverage is not None
            and allocation.get("valuation_coverage") != result.coverage.model_dump()
        ):
            raise ValueError("unbound captured allocation")
        return result, False
    except (ValidationError, ValueError):
        result = AllocationQualification(
            status="missing" if allocation.get("qualification") is None else "invalid",
            reason_code="allocation_valuation_missing"
            if allocation.get("qualification") is None
            else "allocation_valuation_invalid",
        )
        return result, True


def _suppress_weights(allocation: dict[str, Any]) -> None:
    for key in DIMENSION_KEYS.values():
        for row in allocation.get(key, []):
            row["weight"] = None


def _present_snapshot_buckets(
    allocation: dict[str, Any], *, suppress: bool, portfolio_id: str | None
) -> tuple[bool, bool]:
    malformed = unknown = False
    for key in DIMENSION_KEYS.values():
        if key not in allocation:
            continue
        rows = allocation.get(key, [])
        if not isinstance(rows, list):
            malformed = True
            allocation[key] = []
            continue
        admitted = []
        for row in rows:
            if not isinstance(row, dict):
                malformed = True
                continue
            if not isinstance(row.get("group"), str) or not row["group"].strip():
                malformed = True
                continue
            row_malformed, row_unknown = _present_snapshot_row(
                row, suppress=suppress, portfolio_id=portfolio_id
            )
            malformed |= row_malformed
            unknown |= row_unknown
            admitted.append(row)
        allocation[key] = admitted
    return malformed, unknown


def _present_snapshot_row(
    row: dict[str, Any], *, suppress: bool, portfolio_id: str | None
) -> tuple[bool, bool]:
    try:
        malformed = _qualify_contributors(row, portfolio_id=portfolio_id)
    except ReportingUpstreamError:
        malformed = True
        row.update(contributors=[], weight=None, market_value=None)
    unknown = False
    for field in ("weight", "market_value"):
        raw = row.get(field)
        value = allocation_number(raw)
        malformed |= raw is not None and value is None
        row[field] = None if suppress else value
        unknown |= row[field] is None
    return malformed, unknown
