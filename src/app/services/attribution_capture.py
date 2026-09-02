"""Capture of Brinson return attribution from lotus-performance (issue #254).

Answers "why did we outperform?": the active return against the benchmark,
decomposed into allocation, selection and interaction effects per asset
class. lotus-performance computes; Report captures the answer verbatim with
lineage and states what it could establish.

This is the first ASYNC capture. `/performance/attribution` may answer 200
with results, or 202 with a poll path; the client polls within a bounded
budget and, on exhaustion, hands back the accepted envelope. What a pending
attribution means for a report is decided HERE, as a stated posture:

- ``present``  results arrived - stored verbatim;
- ``pending``  the source accepted the calculation and had not completed it
  within the capture's budget. Not a failure and not absence: the work exists
  upstream under a calculation id this capture chose deterministically, so an
  identical retry converges on the SAME calculation instead of resubmitting a
  new one, and a later capture collects the finished result;
- ``unavailable``  the source refused or failed; the reason is bounded.

The section closes on ``pending``/``unavailable`` without failing the report,
per the section-vs-job split - attribution is optional, and denying a client
a report because one optional decomposition was still computing would invert
the split's whole point.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Protocol

#: One level ships first; the hierarchy slot is defined in the contract
#: (each row carries its dimension), so deeper levels ride the same request
#: shape later. asset_class is the vocabulary the allocation page teaches the
#: reader earlier in the same document.
ATTRIBUTION_GROUPING = "asset_class"

#: The presented period, consistent with every other basis statement.
ATTRIBUTION_PERIOD = "YTD"

ATTRIBUTION_ENDPOINT = "/performance/attribution"

#: Namespace for deterministic calculation ids. lotus-performance treats a
#: caller-supplied id as an idempotency handle with VERIFIED semantics: a
#: duplicate id whose replay signature matches returns the existing execution
#: (REPLAY - the convergence this capture wants), and a duplicate id with a
#: different payload is a 409 CONFLICT telling the caller to reuse the
#: original request exactly or pick a new id.
#:
#: The id therefore binds the COMPLETE financial question - it is derived
#: from the canonical serialization of the request body itself, so every
#: input capable of changing the authoritative result (portfolio, window,
#: period, grouping, benchmark, basis, mode, frequency) changes the id, and
#: a field added to the request later is bound automatically rather than
#: remembered. A hand-picked tuple previously bound only portfolio, date,
#: period and grouping: the same id with a DIFFERENT benchmark would have
#: collided with the old calculation as a 409. Same financial question,
#: same identity; different financial question, different identity.
#:
#: Transport-only values never enter the id because they never enter the
#: body: correlation travels in headers, and the id key itself is excluded.
_CALCULATION_ID_NAMESPACE = uuid.UUID("9f4bbf51-2f43-4c56-9d0a-56f0a4f8f1d2")


class AttributionClient(Protocol):
    """What this capture needs from a performance client.

    A Protocol rather than Any, so mypy proves the real client satisfies it at
    the wiring site - a capture calling a method the client does not have is a
    merge-order hazard that should fail statically, not at the first live
    order.
    """

    async def get_attribution(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...


STATUS_PRESENT = "present"
STATUS_PENDING = "pending"
STATUS_UNAVAILABLE = "unavailable"


def attribution_calculation_id(request: dict[str, Any]) -> str:
    """The identity of the financial question this request asks."""

    semantic = {key: value for key, value in request.items() if key != "calculation_id"}
    canonical = json.dumps(semantic, sort_keys=True, separators=(",", ":"))
    return str(uuid.uuid5(_CALCULATION_ID_NAMESPACE, canonical))


def build_attribution_request(
    *,
    portfolio_id: str,
    as_of_date: str,
    benchmark_code: str,
) -> dict[str, Any]:
    """The stateful Brinson request for the presented period.

    The window is the report's YTD convention (January 1st of the as-of year
    through the as-of date), matching the transaction window and the fee-drag
    period. `benchmark_id` is the report's RESOLVED benchmark - the caller
    normalizes aliases, because two surfaces resolving the same code
    differently is the one-fact-two-names defect.
    """

    request: dict[str, Any] = {
        "portfolio_id": portfolio_id,
        "report_start_date": f"{as_of_date[:4]}-01-01",
        "report_end_date": as_of_date,
        "analyses": [{"period": ATTRIBUTION_PERIOD, "frequencies": ["daily"]}],
        "mode": "by_instrument",
        "frequency": "daily",
        "group_by": [ATTRIBUTION_GROUPING],
        "input_mode": "stateful",
        "stateful_input": {
            "metric_basis": "NET",
            "benchmark_id": benchmark_code,
            "dimensions": [ATTRIBUTION_GROUPING],
            "include_cash_flows": True,
        },
    }
    # Stamped from the body it identifies, after the body is complete.
    request["calculation_id"] = attribution_calculation_id(request)
    return request


async def capture_attribution(
    *,
    performance_client: AttributionClient,
    portfolio_id: str,
    as_of_date: str,
    benchmark_code: str,
) -> dict[str, Any]:
    """The attribution section of the snapshot, with its posture stated."""

    request_payload = build_attribution_request(
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        benchmark_code=benchmark_code,
    )
    envelope = {
        "source": {"service": "lotus-performance", "endpoint": ATTRIBUTION_ENDPOINT},
        "request": {
            "calculation_id": request_payload["calculation_id"],
            "period": ATTRIBUTION_PERIOD,
            "group_by": [ATTRIBUTION_GROUPING],
            "metric_basis": "NET",
            "benchmark_code": benchmark_code,
        },
    }

    try:
        status_code, payload = await performance_client.get_attribution(request_payload)
    except Exception:
        return {
            **envelope,
            "status": STATUS_UNAVAILABLE,
            "supportability": _supportability(
                "attribution_upstream_failure",
                "lotus-performance could not be reached for attribution; the "
                "section is unavailable for this capture.",
            ),
        }

    if status_code < 400 and isinstance(payload.get("results_by_period"), dict):
        return {
            **envelope,
            "status": STATUS_PRESENT,
            # Verbatim: the decomposition, its per-period status/reasons, the
            # reconciliation with the source-classified residual, and the
            # model identity are all lotus-performance's statements. Report
            # never rebalances a residual or reweights an effect.
            "results_by_period": payload.get("results_by_period"),
            "model": payload.get("model"),
            "linking": payload.get("linking"),
            "benchmark_context": payload.get("benchmark_context"),
            "calculation_supportability": payload.get("calculation_supportability"),
            "supportability": {"status": "ready", "notes": []},
        }

    if status_code == 202:
        # Accepted, not complete within the capture's budget. The calculation
        # exists upstream under our deterministic id; a rerun converges on it.
        return {
            **envelope,
            "status": STATUS_PENDING,
            "accepted": {
                "calculation_id": _text(payload.get("calculation_id"))
                or request_payload["calculation_id"],
                "result_path": _text(payload.get("result_path")) or None,
            },
            "supportability": _supportability(
                "attribution_accepted_not_complete",
                "lotus-performance accepted the attribution calculation and had "
                "not completed it within the capture budget; regenerating the "
                "report collects the finished result.",
            ),
        }

    reason, message = _refusal(status_code, payload)
    return {
        **envelope,
        "status": STATUS_UNAVAILABLE,
        "supportability": _supportability(reason, message),
    }


def _refusal(status_code: int, payload: dict[str, Any]) -> tuple[str, str]:
    """A bounded reason for a refusal, never a guess.

    409 covers two verified source facts that share a status: a FAILED async
    execution (re-ordering the report is the remedy) and an identity CONFLICT
    - the calculation id exists with a different payload. With the id derived
    from the full request body a conflict should not occur; if it does, it
    means Report's request construction changed across versions for the same
    question, and the source's own detail text (forwarded verbatim below)
    says which of the two happened. They are not split into separate codes
    because the source offers no machine-readable reason - only prose - and
    parsing error strings is the fragility the commentary mapping removed.

    422 is the source saying this portfolio cannot support the calculation
    (missing benchmark assignment, unsupported grouping) - a fact about the
    mandate's data, not a transient. Anything else is a refusal Report does
    not recognise and says so.
    """

    detail = _text(payload.get("detail"))
    if status_code == 409:
        return (
            "attribution_execution_failed",
            detail or "The attribution execution failed at the source.",
        )
    if status_code == 422:
        return (
            "attribution_unsupported_for_portfolio",
            detail or "lotus-performance cannot support attribution for this portfolio's inputs.",
        )
    return (
        "attribution_source_refused",
        detail or "lotus-performance refused the attribution request.",
    )


def _supportability(code: str, message: str) -> dict[str, Any]:
    return {
        "status": "unavailable" if code != "attribution_accepted_not_complete" else "pending",
        "notes": [{"code": code, "severity": "warning", "message": message}],
    }


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""
