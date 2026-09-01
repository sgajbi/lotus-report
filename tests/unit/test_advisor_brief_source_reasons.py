"""The two surfaces that answer "why is there no advisor commentary" must give
the same name to the same upstream fact (issue #166)."""

from typing import Any

import pytest

from app.advisor_brief_source_reasons import (
    SOURCE_REASON_TO_SECTION_REASON,
    section_reason_for,
)
from app.report_ordering_catalogue.advisor_commentary_availability import (
    resolve_advisor_commentary_availability,
)
from app.reporting_metrics import ADVISOR_COMMENTARY_UNAVAILABLE_REASONS


class _LookupStub:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def get_latest_accepted_brief(self, **_: Any) -> tuple[int, dict[str, Any]]:
        return 404, {"metadata": {"reason_code": self._reason}}


@pytest.mark.asyncio
@pytest.mark.parametrize("source_reason", ["no_accepted_run", "no_context_match"])
async def test_availability_names_a_fact_the_way_the_capture_names_it(source_reason):
    """Both surfaces are driven for real, rather than compared through the
    table they share, because the defect this guards against was each surface
    holding its own copy of the mapping and drifting.

    An operator who checks availability, is told "no accepted brief - go accept
    one", orders anyway and is then told "not found - hunt for a missing run"
    has been told the portfolio changed when only the surface did.
    """

    availability = await resolve_advisor_commentary_availability(
        ai_client=_LookupStub(source_reason),
        portfolio_id="PORT-1",
        tenant_id="TENANT-1",
    )

    assert availability.state == "unavailable"
    assert availability.reason_code == section_reason_for(source_reason)


def test_every_mapped_reason_is_a_recorded_metric_reason():
    """A posture the metrics collapse into "other" is invisible on the
    dashboard an operator watches, so the vocabulary has to cover the map."""

    unrecorded = set(SOURCE_REASON_TO_SECTION_REASON.values()) - set(
        ADVISOR_COMMENTARY_UNAVAILABLE_REASONS
    )

    assert not unrecorded


def test_an_unrecognised_reason_maps_to_nothing_rather_than_a_guess():
    """None is a real answer: Report has no interpretation and must say so.
    A default would make an unmapped code indistinguishable from a handled one
    - which is exactly how the previous fall-through hid its own gap."""

    assert section_reason_for("some_future_reason") is None
    assert section_reason_for(None) is None
    assert section_reason_for("") is None
