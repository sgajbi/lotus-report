"""The drawdown capture block (report#289, contract locked with Render).

lotus-risk owns every fact here: the underwater series, the episode
boundaries (peak, trough, recovery - Report NEVER infers episodes from a
return series), the summary, and the methodology/duration vocabulary. The
1Y window pairs with the cumulative chart's x-axis by design.
Benchmark-relative drawdown is deliberately not requested - this panel
answers the portfolio question.
"""

from __future__ import annotations

from typing import Any, Protocol

HTTP_BAD_REQUEST = 400


class _DrawdownRiskClient(Protocol):
    async def drawdown_analytics(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


async def build_drawdown_capture(
    *,
    risk_client: _DrawdownRiskClient,
    portfolio_id: str,
    as_of_date: str,
    reporting_currency: str | None,
    client_id: str | None,
) -> dict[str, object]:
    drawdown_payload: dict[str, object] = {
        "input_mode": "stateful",
        "stateful_input": {
            "portfolio_id": portfolio_id,
            "as_of_date": as_of_date,
            "reporting_currency": reporting_currency,
            "client_id": client_id,
            "net_or_gross": "NET",
            "periods": [{"type": "1Y", "name": "1Y"}],
        },
        "analysis_options": {"include_underwater_series": True},
    }
    try:
        status_code, response_payload = await risk_client.drawdown_analytics(drawdown_payload)
    except Exception:
        status_code, response_payload = 0, {}
    source = {"service": "lotus-risk", "endpoint": "/analytics/risk/drawdown"}
    request = {"period": "1Y", "net_or_gross": "NET", "include_underwater_series": True}
    if status_code >= HTTP_BAD_REQUEST or status_code == 0:
        return {
            "source": source,
            "request": request,
            "supportability": {
                "status": "unavailable",
                "notes": [
                    {
                        "code": "drawdown_upstream_failure",
                        "severity": "blocking",
                        "message": (
                            "Drawdown analytics are unavailable because lotus-risk "
                            "could not calculate them."
                        ),
                    }
                ],
            },
            "results": {},
            "metadata": {},
        }
    return {
        "source": source,
        "request": request,
        "supportability": {"status": "ready", "notes": []},
        "results": _as_dict(response_payload.get("results")),
        "metadata": _as_dict(response_payload.get("metadata")),
    }
