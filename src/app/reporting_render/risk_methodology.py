"""What the risk numbers mean, not just what they are (issue #235).

A tail-risk figure without its basis is not interpretable. "Value at risk 2%"
says nothing on its own: 2% over one day at 95% confidence and 2% over ten
days at 99% are different statements about the same portfolio, and a reader
cannot tell which one they are being shown. The page printed the bare number.

lotus-risk supplies the basis on every VaR metric - method, confidence,
horizon, and how the horizon was scaled - and Report captured it and dropped
it at the package boundary. So did `return_basis`, which decides whether every
risk number on the page is net or gross of fees.

Absent is published as absent, never defaulted. This is the same rule as
`methodology.basis` in contribution ranking, and for the same reason: unlike a
scalar there is no inferring a basis from the value, so a guess is
indistinguishable from a fact. The canonical happy-path fixture carries no VaR
`details` at all, which is exactly the case a default would silently paper
over.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

#: The period the portfolio review presents. The capture requests YTD and 1Y;
#: the page shows YTD, and a basis must describe the figure beside it rather
#: than some other period's.
PRESENTED_PERIOD = "YTD"


def build_risk_methodology(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The basis for the risk figures on the page, or nothing at all."""

    risk_analytics = _as_dict(snapshot.get("riskAnalytics"))
    if not risk_analytics:
        # Section not ordered; there are no figures to qualify.
        return {}

    methodology = _as_dict(risk_analytics.get("methodology"))
    details = _var_details(risk_analytics)

    return {
        "return_basis": _text(methodology.get("return_basis")) or None,
        "value_at_risk": {
            "method": _text(details.get("method")) or None,
            "confidence_pct": _confidence_pct(details.get("confidence")),
            "horizon_days": _int_text(details.get("horizon_days")),
            "horizon_scale_method": _text(details.get("horizon_scale_method")) or None,
        },
    }


def _var_details(risk_analytics: dict[str, Any]) -> dict[str, Any]:
    results = _as_dict(risk_analytics.get("results"))
    period = _as_dict(results.get(PRESENTED_PERIOD))
    metrics = _as_dict(period.get("metrics"))
    return _as_dict(_as_dict(metrics.get("VAR")).get("details"))


def _confidence_pct(value: Any) -> str | None:
    """lotus-risk states confidence as a ratio; the page reads a percentage.

    Converted here rather than by Render: 0.95 and 95 are both plausible as a
    confidence figure, so a renderer meeting one cannot tell which convention
    it was handed.
    """

    confidence = _decimal(value)
    if confidence is None:
        return None
    return f"{(confidence * 100).quantize(Decimal('0.01'))}%"


def _int_text(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return str(value)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
