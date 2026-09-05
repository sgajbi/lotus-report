"""ADVISOR_COMMENTARY resolution: exact accepted narrative in, bounded
section package out, with the section-vs-job failure split (issue #166)."""

import pytest

from app.reporting_lineage.advisor_commentary import (
    AdvisorCommentarySourceUnavailableError,
    advisor_commentary_requested,
    requested_advisor_brief_run_id,
    resolve_advisor_commentary_package,
)


def _accepted_payload(**overrides) -> dict:
    payload = {
        "schema_id": "lotus-ai.workflow_pack_run.accepted_output.advisor_brief.v1",
        "service": "lotus-ai",
        "version": "1.0.0",
        "run_id": "run_accept_1",
        "pack_id": "advisor_brief.pack",
        "pack_family": "advisor_brief",
        "pack_version": "v1",
        "task_id": "task_1",
        "request_id": "req_1",
        "tenant_id": "tenant-sg",
        "workflow_authority_owner": "lotus-performance",
        "review": {"reviewed_by": "advisor-lead-7", "reviewed_at": "2026-08-28T10:00:00Z"},
        "advisor_brief_status": "complete",
        "coverage_state": "full",
        "grounded_summary": "The portfolio outperformed its benchmark this period.",
        "talking_points": [
            {
                "headline": "Equity allocation drove returns",
                "detail": "Overweight global equities contributed 1.2%.",
                "tone": "positive",
                "evidence_refs": [
                    {
                        "metric_label": "Equity Contribution",
                        "metric_value": "1.2%",
                        "source_ref": "performance:contribution:equities",
                    }
                ],
            }
        ],
        "risks_and_exceptions": [
            {
                "headline": "Concentration in technology",
                "detail": "Top sector weight exceeds policy guidance.",
                "tone": "warning",
                "evidence_refs": [
                    {
                        "metric_label": "Top Sector Weight",
                        "metric_value": "34%",
                        "source_ref": "risk:concentration:sector",
                    }
                ],
            }
        ],
        "context": {
            "portfolio_id": "PB_SG_GLOBAL_BAL_001",
            "period": "YTD",
            "as_of_date": "2026-08-22",
            "reporting_currency": "USD",
            "benchmark": None,
        },
        "source_refs": ["performance:workspace-summary"],
        "evidence_types": ["metric_evidence"],
        "content_hash": "0a" * 32,
        "output_validation": {
            "validation_state": "VALIDATED",
            "authority": "non_authoritative_ai_output",
            "ruleset_version": "output-validation.v4",
        },
        "content_hash_algorithm": "sha256",
        "notes": ["Review-gated projection; not client-release certification."],
    }
    payload.update(overrides)
    return payload


class _StubClient:
    def __init__(self, status_code: int, payload: dict):
        self._status_code = status_code
        self._payload = payload
        self.calls: list[tuple[str, str]] = []

    async def get_accepted_workflow_output(self, run_id: str, *, tenant_id: str):
        self.calls.append((run_id, tenant_id))
        return self._status_code, self._payload


class _BrokenClient:
    async def get_accepted_workflow_output(self, run_id: str, *, tenant_id: str):
        raise ConnectionError("network down")


async def _resolve(client, **overrides) -> dict:
    kwargs = {
        "client": client,
        "run_id": "run_accept_1",
        "tenant_id": "tenant-sg",
        "portfolio_id": "PB_SG_GLOBAL_BAL_001",
        "as_of_date": "2026-08-22",
        "reporting_currency": "USD",
    }
    kwargs.update(overrides)
    return await resolve_advisor_commentary_package(**kwargs)


@pytest.mark.asyncio
async def test_accepted_run_composes_included_package_with_disclosure():
    client = _StubClient(200, _accepted_payload())
    package = await _resolve(client)

    assert package["status"] == "included"
    assert package["run_id"] == "run_accept_1"
    assert package["review"] == {
        "reviewed_by": "advisor-lead-7",
        "reviewed_at": "2026-08-28T10:00:00Z",
    }
    assert package["grounded_summary"].startswith("The portfolio outperformed")
    assert package["talking_points"][0]["headline"] == "Equity allocation drove returns"
    assert package["risks_and_exceptions"][0]["tone"] == "warning"
    assert package["content_hash"] == "0a" * 32
    assert package["content_hash_algorithm"] == "sha256"
    assert "reviewed by advisor-lead-7" in package["disclosure_text"]
    assert "run run_accept_1" in package["disclosure_text"]
    assert client.calls == [("run_accept_1", "tenant-sg")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_reason", "expected"),
    [
        ("run_not_completed", "advisor_brief_not_reviewed"),
        ("run_not_accepted", "advisor_brief_not_reviewed"),
        ("run_superseded", "advisor_brief_not_reviewed"),
        ("pack_projection_unsupported", "advisor_brief_not_found"),
        ("output_artifact_missing", "advisor_brief_not_found"),
        ("output_artifact_malformed", "advisor_brief_not_found"),
    ],
)
async def test_definitive_source_postures_close_the_section(source_reason, expected):
    client = _StubClient(409, {"detail": "refused", "metadata": {"reason_code": source_reason}})
    package = await _resolve(client)
    assert package["status"] == "unavailable"
    assert package["reason_code"] == expected
    assert package["advisor_brief_run_id"] == "run_accept_1"


@pytest.mark.asyncio
async def test_unknown_run_maps_to_not_found():
    client = _StubClient(404, {"detail": "not found"})
    package = await _resolve(client)
    assert package["status"] == "unavailable"
    assert package["reason_code"] == "advisor_brief_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "detail_fragment"),
    [
        ("portfolio_id", "PB_OTHER", "brief portfolio"),
        ("as_of_date", "2026-07-31", "brief as_of_date"),
        ("reporting_currency", "SGD", "brief reporting_currency"),
    ],
)
async def test_asserted_context_conflicts_close_the_section(field, value, detail_fragment):
    context = _accepted_payload()["context"] | {field: value}
    client = _StubClient(200, _accepted_payload(context=context))
    package = await _resolve(client)
    assert package["status"] == "unavailable"
    assert package["reason_code"] == "advisor_brief_context_mismatch"
    assert detail_fragment in package["detail"]


@pytest.mark.asyncio
async def test_unasserted_context_nulls_never_conflict():
    context = _accepted_payload()["context"] | {"as_of_date": None, "reporting_currency": None}
    client = _StubClient(200, _accepted_payload(context=context))
    package = await _resolve(client)
    assert package["status"] == "included"
    assert package["context"]["as_of_date"] is None


@pytest.mark.asyncio
async def test_missing_review_identity_or_hash_blocks_disclosure():
    for overrides in (
        {"review": {"reviewed_by": "", "reviewed_at": "2026-08-28T10:00:00Z"}},
        {"review": {"reviewed_by": "advisor-lead-7", "reviewed_at": ""}},
        {"content_hash": ""},
    ):
        client = _StubClient(200, _accepted_payload(**overrides))
        package = await _resolve(client)
        assert package["status"] == "unavailable"
        assert package["reason_code"] == "ai_disclosure_policy_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_authorization_failures_fail_capture_not_the_section(status_code):
    """A refused lotus-report caller is an environment-wide deployment fault;
    closing the section would mask it as a missing brief on every order."""

    client = _StubClient(status_code, {"detail": "caller refused"})
    with pytest.raises(AdvisorCommentarySourceUnavailableError, match="access-control"):
        await _resolve(client)


@pytest.mark.asyncio
async def test_wrong_schema_or_run_identity_closes_the_section():
    """A 200 body carrying a different schema or run identity must never be
    archived under this contract's provenance.

    This is the source violating its contract, not withholding a brief: it
    answered successfully for something other than what was asked."""

    wrong_schema = _StubClient(200, _accepted_payload(schema_id="other.schema.v9"))
    package = await _resolve(wrong_schema)
    assert package["status"] == "unavailable"
    assert package["reason_code"] == "advisor_brief_source_contract_violation"
    assert "schema_id" in package["detail"]

    missing_schema = _StubClient(200, _accepted_payload(schema_id=None))
    package = await _resolve(missing_schema)
    assert package["status"] == "unavailable"
    assert "missing" in package["detail"]

    wrong_run = _StubClient(200, _accepted_payload(run_id="run_other"))
    package = await _resolve(wrong_run)
    assert package["status"] == "unavailable"
    assert package["reason_code"] == "advisor_brief_source_contract_violation"
    assert "run_other" in package["detail"]


@pytest.mark.asyncio
async def test_withheld_and_contract_violating_briefs_are_told_apart():
    """The two ways a brief can lack a validation verdict are opposite facts,
    and the job event carries only the reason code - no detail - so the code
    itself has to carry the difference.

    A 409 with output_not_validated is lotus-ai working correctly: it ran the
    validation, the verdict was not VALIDATED, and it withheld the brief. The
    operator re-runs the brief so it acquires a verdict.

    A 200 whose verdict block is missing or not VALIDATED is lotus-ai
    publishing what it promised to refuse. Re-running the brief is the wrong
    instruction there - nothing about this run was the problem, and a
    guarantee upstream has regressed. Collapsing both into one reason would
    send an operator to do the wrong thing with no way to tell.
    """

    withheld = _StubClient(409, {"metadata": {"reason_code": "output_not_validated"}})
    withheld_package = await _resolve(withheld)

    violated = _StubClient(200, _accepted_payload(output_validation=None))
    violated_package = await _resolve(violated)

    assert withheld_package["reason_code"] == "advisor_brief_not_validated"
    assert violated_package["reason_code"] == "advisor_brief_source_contract_violation"
    assert withheld_package["reason_code"] != violated_package["reason_code"]

    # Both close the section: the document is the same either way.
    assert withheld_package["status"] == "unavailable"
    assert violated_package["status"] == "unavailable"


@pytest.mark.asyncio
async def test_evidence_refs_keep_only_complete_typed_grounding():
    """lotus-ai#189 projects grounding as AdvisorBriefAcceptedEvidenceRef
    dicts ({metric_label, metric_value, source_ref}, all required). Report
    keeps complete refs verbatim and drops any other shape rather than
    inventing partial grounding - including the legacy string shape that
    never carried real data."""

    payload = _accepted_payload(
        talking_points=[
            {
                "headline": "H",
                "detail": "D",
                "tone": "positive",
                "evidence_refs": [
                    {
                        "metric_label": "Active Return",
                        "metric_value": "-6.68%",
                        "source_ref": "lotus-gateway:performance-summary:YTD",
                    },
                    {"metric_label": "Missing value", "source_ref": "x"},
                    "legacy-string-ref",
                    {"metric_label": " ", "metric_value": "1", "source_ref": "y"},
                ],
            }
        ]
    )
    package = await _resolve(_StubClient(200, payload))
    assert package["talking_points"][0]["evidence_refs"] == [
        {
            "metric_label": "Active Return",
            "metric_value": "-6.68%",
            "source_ref": "lotus-gateway:performance-summary:YTD",
        }
    ]


@pytest.mark.asyncio
async def test_tone_is_normalized_to_the_closed_set():
    """lotus-ai guarantees {positive, neutral, warning}; the composition
    boundary re-asserts it so the render template can map tones to design
    tokens against exact literals."""

    payload = _accepted_payload(
        talking_points=[
            {"headline": "H", "detail": "D", "tone": "warning", "evidence_refs": []},
            {"headline": "H2", "detail": "D2", "tone": "exuberant", "evidence_refs": []},
            {"headline": "H3", "detail": "D3", "tone": "", "evidence_refs": []},
        ]
    )
    package = await _resolve(_StubClient(200, payload))
    assert [item["tone"] for item in package["talking_points"]] == [
        "warning",
        "neutral",
        "neutral",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [500, 503, 429])
async def test_transient_source_failures_raise_for_capture_retry(status_code):
    client = _StubClient(status_code, {"detail": "unavailable"})
    with pytest.raises(AdvisorCommentarySourceUnavailableError):
        await _resolve(client)


@pytest.mark.asyncio
async def test_network_failure_raises_for_capture_retry():
    with pytest.raises(AdvisorCommentarySourceUnavailableError):
        await _resolve(_BrokenClient())


def test_request_detection_helpers():
    assert advisor_commentary_requested({"sections": ["OVERVIEW", "advisor_commentary"]}) is True
    assert advisor_commentary_requested({"sections": ["OVERVIEW"]}) is False
    assert advisor_commentary_requested({}) is False
    assert advisor_commentary_requested({"sections": "ADVISOR_COMMENTARY"}) is False
    assert requested_advisor_brief_run_id({"advisor_brief_run_id": " run_1 "}) == "run_1"
    assert requested_advisor_brief_run_id({"advisor_brief_run_id": "  "}) is None
    assert requested_advisor_brief_run_id({}) is None


@pytest.mark.asyncio
async def test_a_brief_written_about_another_period_is_not_admissible():
    """A brief about YTD does not describe a Q3 report, however accurate it is
    about YTD (issue #166 admissibility)."""

    client = _StubClient(200, _accepted_payload())
    package = await _resolve(client, report_period="Q3")

    assert package["status"] == "unavailable"
    assert package["reason_code"] == "advisor_brief_context_mismatch"
    assert "period" in package["detail"]


@pytest.mark.asyncio
async def test_a_brief_written_against_another_benchmark_is_not_admissible():
    """Relative-performance narrative is only true of the benchmark it was
    written against."""

    payload = _accepted_payload()
    payload["context"] = {**payload["context"], "benchmark": "BMK_OTHER"}
    client = _StubClient(200, payload)

    package = await _resolve(client, benchmark_code="BMK_PB_GLOBAL_BALANCED_60_40")

    assert package["status"] == "unavailable"
    assert package["reason_code"] == "advisor_brief_context_mismatch"
    assert "benchmark" in package["detail"]


@pytest.mark.asyncio
async def test_unasserted_period_and_benchmark_still_never_conflict():
    """A null source value means "not asserted" and must not close a section
    the source never disagreed with - the existing rule, extended to the two
    new dimensions."""

    payload = _accepted_payload()
    payload["context"] = {**payload["context"], "period": None, "benchmark": None}
    client = _StubClient(200, payload)

    package = await _resolve(
        client, report_period="Q3", benchmark_code="BMK_PB_GLOBAL_BALANCED_60_40"
    )

    assert package["status"] == "included"


@pytest.mark.asyncio
async def test_a_foreign_tenant_projection_is_refused_even_though_ai_fails_closed():
    """lotus-ai is tenant-scoped and should never return another tenant's run,
    which is exactly why the echo is asserted rather than assumed: composing
    one tenant's reviewed narrative into another tenant's client document is
    the worst outcome this section has."""

    client = _StubClient(200, _accepted_payload(tenant_id="tenant-uk"))
    package = await _resolve(client)

    assert package["status"] == "unavailable"
    assert package["reason_code"] == "advisor_brief_source_contract_violation"
    assert "tenant" in package["detail"]


@pytest.mark.asyncio
async def test_a_brief_without_grounding_sources_cannot_carry_its_disclosure():
    """The mandated provenance line names the reviewer, the run AND the
    sources; with no source refs it cannot be rendered truthfully, so the
    section closes rather than shipping narrative under an empty disclosure."""

    client = _StubClient(200, _accepted_payload(source_refs=[]))
    package = await _resolve(client)

    assert package["status"] == "unavailable"
    assert package["reason_code"] == "ai_disclosure_policy_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,expected",
    [
        ("output_not_validated", "advisor_brief_not_validated"),
        ("lookup_scan_saturated", "advisor_brief_source_unproven"),
        ("no_context_match", "advisor_brief_context_mismatch"),
        ("no_accepted_run", "advisor_brief_not_reviewed"),
        ("pack_projection_unsupported", "advisor_brief_not_found"),
        ("run_superseded", "advisor_brief_not_reviewed"),
    ],
)
async def test_every_source_refusal_maps_to_its_own_posture(reason, expected):
    """lotus-ai's bounded reasons are mapped exhaustively rather than collapsed.

    `output_not_validated` is the one refusal where the artifact EXISTS and was
    FOUND and was withheld on authority grounds - the operator action is to
    re-run the brief so it acquires a verdict, not to hunt for a missing run.
    `lookup_scan_saturated` is retryable and lotus-ai deliberately excludes it
    from its own not-found set.
    """

    client = _StubClient(409, {"metadata": {"reason_code": reason}})
    package = await _resolve(client)

    assert package["status"] == "unavailable"
    assert package["reason_code"] == expected


@pytest.mark.asyncio
async def test_an_unrecognised_refusal_never_claims_a_cause_it_cannot_name():
    """The old fall-through asserted `advisor_brief_not_found` for any code it
    did not know, sending an operator after a missing run when the brief was
    withheld for a reason report simply had not mapped. That default is also
    what let the gap live: an unmapped code looked like a handled one."""

    client = _StubClient(409, {"metadata": {"reason_code": "some_future_reason"}})
    package = await _resolve(client)

    assert package["reason_code"] == "advisor_brief_source_refused"


@pytest.mark.asyncio
async def test_an_unadorned_404_still_means_absence():
    """A 404 with no reason code is the one status that means absence on its
    own, so it must not become the unrecognised-refusal posture."""

    client = _StubClient(404, {})
    package = await _resolve(client)

    assert package["reason_code"] == "advisor_brief_not_found"


@pytest.mark.asyncio
async def test_an_unproven_lookup_closes_the_section_without_failing_the_capture():
    """`unproven` is not `unavailable-transport`. Raising the capture-retryable
    error would burn the retry budget on an identical request that saturates an
    identical bound, and would eventually fail the whole JOB - denying the
    client a report over one optional section. The condition clears through an
    operator action, so the section closes and the report completes.

    Only a saturated scan is unproven. `no_context_match` is a definitive
    answer and is covered by the shared-vocabulary test instead."""

    client = _StubClient(409, {"metadata": {"reason_code": "lookup_scan_saturated"}})

    package = await _resolve(client)

    assert package["status"] == "unavailable"
    assert package["reason_code"] == "advisor_brief_source_unproven"


@pytest.mark.asyncio
async def test_a_reviewed_claim_states_whether_it_is_checkable():
    """An AI-drafted claim with visible grounding is checkable; without it the
    same sentence is an assertion. Render can otherwise tell the difference
    only by CONTRAST with grounded points on the same page - a signal that
    disappears exactly when every point is ungrounded - so the state is stated
    rather than inferred from an empty list (issue #166)."""

    payload = _accepted_payload()
    payload["talking_points"] = [
        {
            "headline": "Equity allocation drove returns",
            "detail": "Overweight global equities contributed 1.2%.",
            "tone": "positive",
            "evidence_refs": [
                {
                    "metric_label": "Equity Contribution",
                    "metric_value": "1.2%",
                    "source_ref": "performance:contribution:equities",
                }
            ],
        },
        {
            "headline": "Positioning remains appropriate",
            "detail": "No change recommended.",
            "tone": "neutral",
            "evidence_refs": [],
        },
    ]
    client = _StubClient(200, payload)

    package = await _resolve(client)

    points = package["talking_points"]
    assert points[0]["grounding"] == "grounded"
    assert points[1]["grounding"] == "ungrounded"
    # Silent removal of reviewed content is never the answer: the claim a
    # named reviewer accepted still reaches the page, saying what it is.
    assert points[1]["headline"] == "Positioning remains appropriate"
    assert "unusable_evidence_ref_count" not in points[1]


@pytest.mark.asyncio
async def test_unreadable_grounding_is_distinguished_from_none_offered():
    """The page draws both the same - not checkable either way - but an
    operator needs the difference between "cited nothing" and "cited
    unreadably"."""

    payload = _accepted_payload()
    payload["talking_points"] = [
        {
            "headline": "Duration was reduced",
            "detail": "Shortened by 0.4 years.",
            "tone": "neutral",
            "evidence_refs": [{"metric_label": "Duration"}, {"source_ref": "risk:duration"}],
        }
    ]
    client = _StubClient(200, payload)

    package = await _resolve(client)

    point = package["talking_points"][0]
    assert point["grounding"] == "ungrounded"
    assert point["evidence_refs"] == []
    assert point["unusable_evidence_ref_count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed",
    [
        {"metric_label": "Duration", "metric_value": "0.4", "source_ref": "risk:duration"},
        "perf:ytd:twr",
        {"unexpected": "shape"},
    ],
)
async def test_a_malformed_reference_container_still_counts_as_offered(malformed):
    """Review finding on PR #229: counting only list containers let a citation
    that arrived in the wrong shape - a single reference object after schema
    drift, say - look identical to a claim that cited nothing. That is exactly
    the distinction the count exists to preserve."""

    payload = _accepted_payload()
    payload["talking_points"] = [
        {
            "headline": "Duration was reduced",
            "detail": "Shortened by 0.4 years.",
            "tone": "neutral",
            "evidence_refs": malformed,
        }
    ]
    client = _StubClient(200, payload)

    package = await _resolve(client)

    point = package["talking_points"][0]
    assert point["grounding"] == "ungrounded"
    assert point["evidence_refs"] == []
    assert point["unusable_evidence_ref_count"] == 1


@pytest.mark.asyncio
async def test_a_claim_that_offered_nothing_reports_no_unusable_count():
    """The other side of the same distinction: absence is not malformation."""

    payload = _accepted_payload()
    payload["talking_points"] = [
        {"headline": "No change", "detail": "Hold.", "tone": "neutral", "evidence_refs": []}
    ]
    client = _StubClient(200, payload)

    package = await _resolve(client)

    assert package["talking_points"][0]["grounding"] == "ungrounded"
    assert "unusable_evidence_ref_count" not in package["talking_points"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "verdict,fragment",
    [
        (None, "no validation verdict"),
        ({"validation_state": "UNVALIDATED_LOCAL_ONLY"}, "UNVALIDATED_LOCAL_ONLY"),
        ({"validation_state": "VALIDATED", "ruleset_version": "v4"}, "authority"),
        (
            {"validation_state": "VALIDATED", "authority": "non_authoritative_ai_output"},
            "ruleset version",
        ),
    ],
)
async def test_narrative_without_a_complete_validated_verdict_is_inadmissible(verdict, fragment):
    """On a 200, lotus-ai guarantees a complete VALIDATED block: it refuses
    non-VALIDATED output with a 409 and fails closed on incomplete evidence
    (ai#231). So every deviation here is the SOURCE VIOLATING ITS CONTRACT, not
    a brief it withheld - a distinction that matters because the operator
    actions are opposite: investigate lotus-ai, versus re-run the brief.

    Report checks anyway because it composes this narrative verbatim into an
    archived client document: if the upstream guarantee regresses, the first
    place it becomes durable is a governed PDF, by which point it is evidence
    rather than a bug."""

    payload = _accepted_payload()
    if verdict is None:
        payload.pop("output_validation")
    else:
        payload["output_validation"] = verdict
    client = _StubClient(200, payload)

    package = await _resolve(client)

    assert package["status"] == "unavailable"
    assert package["reason_code"] == "advisor_brief_source_contract_violation"
    assert fragment in package["detail"]


@pytest.mark.asyncio
async def test_a_complete_validated_verdict_is_admissible():
    package = await _resolve(_StubClient(200, _accepted_payload()))

    assert package["status"] == "included"


@pytest.mark.asyncio
async def test_an_integrity_mismatch_closes_the_section_and_never_regenerates() -> None:
    """lotus-ai#328 conformance: output_artifact_integrity_mismatch means
    the reviewed artifact's bytes changed after acceptance. Report states
    the integrity posture explicitly - an unavailable section with its own
    bounded reason - and neither retries (the status is 4xx, not
    retryable-transport) nor regenerates narrative."""

    class _IntegrityRefusingClient:
        async def get_accepted_workflow_output(self, run_id, *, tenant_id):
            return 409, {
                "error": "LOTUS_AI_ACCEPTED_OUTPUT_REFUSED",
                "metadata": {"reason_code": "output_artifact_integrity_mismatch"},
            }

    package = await resolve_advisor_commentary_package(
        client=_IntegrityRefusingClient(),
        run_id="run_integrity",
        tenant_id="tenant-sg",
        portfolio_id="P1",
        as_of_date="2026-04-22",
        reporting_currency="USD",
    )

    assert package["status"] == "unavailable"
    assert package["reason_code"] == "advisor_brief_source_integrity_failed"
