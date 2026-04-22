from decimal import Decimal, InvalidOperation

ADVISOR_ROUTE_TARGETS = {
    "workbench_review": ("lotus-workbench", "portfolio_review"),
    "performance_review": ("lotus-performance", "performance_review"),
    "risk_review": ("lotus-risk", "risk_review"),
    "proposal_context": ("lotus-advise", "proposal_context"),
    "action_register": ("lotus-manage", "action_register"),
}


def build_advisor_sections(
    *,
    portfolio_id: str,
    as_of_date: str,
    response: dict[str, object],
    client_sections: list[dict[str, object]],
) -> list[dict[str, object]]:
    prompts = [
        _readiness_prompt(
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
            response=response,
            client_sections=client_sections,
        )
    ]

    construction_prompt = _portfolio_construction_prompt(
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        response=response,
    )
    if construction_prompt is not None:
        prompts.append(construction_prompt)

    performance_prompt = _performance_prompt(
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        response=response,
    )
    if performance_prompt is not None:
        prompts.append(performance_prompt)

    risk_prompt = _risk_prompt(
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        response=response,
    )
    if risk_prompt is not None:
        prompts.append(risk_prompt)

    return [
        {
            "section_id": "advisor_discussion",
            "title": "Advisor Discussion And Follow-Up",
            "status": "ready",
            "items": prompts,
        }
    ]


def _readiness_prompt(
    *,
    portfolio_id: str,
    as_of_date: str,
    response: dict[str, object],
    client_sections: list[dict[str, object]],
) -> dict[str, object]:
    readiness = _as_dict(response.get("readiness"))
    unavailable_sections = [
        _safe_str(section.get("title"))
        for section in client_sections
        if section.get("status") == "unavailable"
    ]
    if unavailable_sections:
        detail = " unavailable client sections: " + ", ".join(unavailable_sections)
    else:
        detail = " no unavailable client sections"
    return _advisor_prompt(
        prompt_id="review_readiness",
        prompt=(
            f"Confirm report readiness is {_safe_str(readiness.get('status')) or 'ready'} "
            f"for {portfolio_id} as of {as_of_date} with{detail}."
        ),
        source_section_ids=_ready_or_requested_section_ids(client_sections),
        route_keys=("workbench_review", "action_register"),
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        response=response,
    )


def _portfolio_construction_prompt(
    *,
    portfolio_id: str,
    as_of_date: str,
    response: dict[str, object],
) -> dict[str, object] | None:
    overview = _as_dict(response.get("overview"))
    allocation = _as_dict(response.get("allocation"))
    holdings = _as_dict(response.get("holdings"))
    if not overview and not allocation and not holdings:
        return None

    facts: list[str] = []
    total_market_value = _optional_number(overview.get("total_market_value"))
    if total_market_value is not None:
        facts.append(
            "total market value "
            + _format_money(total_market_value, _safe_str(overview.get("currency")))
        )
    total_cash = _optional_number(overview.get("total_cash"))
    if total_cash is not None:
        facts.append("cash " + _format_money(total_cash, _safe_str(overview.get("currency"))))
    top_allocation = _top_allocation_bucket(allocation)
    if top_allocation is not None:
        facts.append(
            f"largest allocation {top_allocation['group']} at "
            f"{_format_percent(_to_decimal(top_allocation['weight']) * Decimal('100'))}"
        )
    position_count = _to_int(holdings.get("positionCount"))
    if position_count:
        facts.append(f"{position_count} sourced positions")
    if not facts:
        return None

    return _advisor_prompt(
        prompt_id="portfolio_construction_review",
        prompt="Discuss portfolio construction using " + ", ".join(facts) + ".",
        source_section_ids=[
            section_id
            for section_id, payload in (
                ("executive_summary", overview),
                ("asset_allocation", allocation),
                ("holdings_appendix", holdings),
            )
            if payload
        ],
        route_keys=("workbench_review", "proposal_context", "action_register"),
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        response=response,
    )


def _performance_prompt(
    *,
    portfolio_id: str,
    as_of_date: str,
    response: dict[str, object],
) -> dict[str, object] | None:
    performance = _as_dict(response.get("performance"))
    if not performance:
        return None
    summary = _as_dict(performance.get("summary"))
    ytd = _as_dict(summary.get("YTD"))
    net_cumulative_return = _optional_number(ytd.get("net_cumulative_return"))
    benchmark_code = _safe_str(_as_dict(performance.get("benchmark")).get("benchmark_code"))
    facts = []
    if net_cumulative_return is not None:
        facts.append(f"YTD net cumulative return {_format_percent(net_cumulative_return)}")
    if benchmark_code:
        facts.append(f"benchmark {benchmark_code}")
        comparison_status = _safe_str(
            _as_dict(performance.get("benchmark")).get("comparison_status")
        )
        if comparison_status == "unavailable":
            facts.append("benchmark comparison is not source-backed in this report")
    facts.append("sub-year annualized returns are suppressed unless source support is explicit")
    return _advisor_prompt(
        prompt_id="performance_discussion",
        prompt="Discuss performance using " + ", ".join(facts) + ".",
        source_section_ids=["performance_review"],
        route_keys=("workbench_review", "performance_review", "proposal_context"),
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        response=response,
    )


def _risk_prompt(
    *,
    portfolio_id: str,
    as_of_date: str,
    response: dict[str, object],
) -> dict[str, object] | None:
    risk = _as_dict(response.get("riskAnalytics"))
    if not risk:
        return None
    supportability = _as_dict(risk.get("supportability"))
    notes = [_as_dict(note) for note in _as_list(supportability.get("notes"))]
    ytd_summary = _as_dict(_as_dict(risk.get("summary")).get("YTD"))
    facts = []
    volatility = _optional_number(ytd_summary.get("volatility"))
    drawdown = _optional_number(ytd_summary.get("drawdown"))
    value_at_risk = _optional_number(ytd_summary.get("value_at_risk"))
    if volatility is not None:
        facts.append(f"volatility {_format_percent(volatility)}")
    if drawdown is not None:
        facts.append(f"drawdown {_format_percent(drawdown)}")
    if value_at_risk is not None:
        facts.append(f"value at risk {_format_percent(value_at_risk)}")
    limitation_messages = [
        _safe_str(note.get("message"))
        for note in notes
        if _safe_str(note.get("severity")) in {"warning", "blocking"}
        and _safe_str(note.get("message"))
    ]
    if limitation_messages:
        prompt = (
            "Discuss YTD risk posture using "
            + (", ".join(facts) if facts else "available sourced risk metrics")
            + ". Supportability limitation: "
            + " ".join(limitation_messages)
        )
    else:
        prompt = (
            "Discuss YTD risk posture using "
            + (", ".join(facts) if facts else "available sourced risk metrics")
            + "."
        )
    return _advisor_prompt(
        prompt_id="risk_discussion",
        prompt=prompt,
        source_section_ids=["risk_review"],
        route_keys=("workbench_review", "risk_review", "action_register"),
        portfolio_id=portfolio_id,
        as_of_date=as_of_date,
        response=response,
    )


def _advisor_prompt(
    *,
    prompt_id: str,
    prompt: str,
    source_section_ids: list[str],
    route_keys: tuple[str, ...],
    portfolio_id: str,
    as_of_date: str,
    response: dict[str, object],
) -> dict[str, object]:
    return {
        "prompt_id": prompt_id,
        "advisor_only": True,
        "prompt": prompt,
        "source_section_ids": source_section_ids,
        "source_refs": _source_refs(response=response, source_section_ids=source_section_ids),
        "route_targets": _route_targets(
            portfolio_id=portfolio_id,
            as_of_date=as_of_date,
            route_keys=route_keys,
        ),
    }


def _route_targets(
    *,
    portfolio_id: str,
    as_of_date: str,
    route_keys: tuple[str, ...],
) -> list[dict[str, object]]:
    targets: list[dict[str, object]] = []
    for target_key in route_keys:
        surface, route_key = ADVISOR_ROUTE_TARGETS[target_key]
        targets.append(
            {
                "target_id": target_key,
                "surface": surface,
                "route_key": route_key,
                "portfolio_id": portfolio_id,
                "as_of_date": as_of_date,
                "mutation_allowed": False,
            }
        )
    return targets


def _source_refs(
    *,
    response: dict[str, object],
    source_section_ids: list[str],
) -> list[dict[str, object]]:
    source_section_set = set(source_section_ids)
    refs = []
    evidence = _as_dict(response.get("evidence"))
    for source_ref in _as_list(evidence.get("source_refs")):
        source_ref_payload = _as_dict(source_ref)
        if source_ref_payload.get("section_id") in source_section_set:
            refs.append(
                {
                    "section_id": source_ref_payload.get("section_id"),
                    "source_service": source_ref_payload.get("source_service"),
                    "source_endpoint": source_ref_payload.get("source_endpoint"),
                }
            )
    return refs


def _ready_or_requested_section_ids(client_sections: list[dict[str, object]]) -> list[str]:
    return [
        _safe_str(section.get("section_id"))
        for section in client_sections
        if section.get("status") != "omitted_by_request"
    ]


def _top_allocation_bucket(allocation: dict[str, object]) -> dict[str, object] | None:
    buckets: list[dict[str, object]] = []
    asset_class_buckets = _as_list(allocation.get("byAssetClass"))
    source_buckets = asset_class_buckets if asset_class_buckets else list(allocation.values())
    for value in source_buckets:
        if isinstance(value, list):
            buckets.extend(_as_dict(item) for item in _as_list(value))
        else:
            buckets.append(_as_dict(value))
    if not buckets:
        return None
    return max(buckets, key=lambda bucket: _to_decimal(bucket.get("weight")))


def _optional_number(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return _to_decimal(value)
    except InvalidOperation:
        return None


def _format_money(value: Decimal, currency: str) -> str:
    amount = f"{value:.2f}"
    return f"{currency} {amount}" if currency else amount


def _format_percent(value: Decimal) -> str:
    return f"{value:.2f}%"


def _as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    return {}


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return []


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    if isinstance(value, (int, str)) or value.__class__ is type(0.0):
        return Decimal(str(value))
    raise InvalidOperation


def _to_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _safe_str(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""
