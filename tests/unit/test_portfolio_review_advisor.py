from decimal import Decimal

from app.services.portfolio_review_advisor import build_advisor_sections


def test_advisor_sections_include_fact_backed_prompts_and_readiness_detail():
    response = {
        "readiness": {"status": "partial"},
        "evidence": {
            "source_refs": [
                {
                    "section_id": "executive_summary",
                    "source_service": "lotus-core",
                    "source_endpoint": "/reporting/portfolio-summary/query",
                },
                {
                    "section_id": "asset_allocation",
                    "source_service": "lotus-core",
                    "source_endpoint": "/reporting/asset-allocation/query",
                },
                {
                    "section_id": "holdings_appendix",
                    "source_service": "lotus-core",
                    "source_endpoint": "/portfolios/P1/positions",
                },
                {
                    "section_id": "performance_review",
                    "source_service": "lotus-performance",
                    "source_endpoint": "/performance/workspace-summary",
                },
                {
                    "section_id": "risk_review",
                    "source_service": "lotus-risk",
                    "source_endpoint": "/analytics/risk/calculate",
                },
            ]
        },
        "overview": {
            "total_market_value": "1000000.5",
            "total_cash": "50000",
            "currency": "USD",
        },
        "allocation": {
            "byAssetClass": [
                {"group": "Cash", "weight": None},
                {"group": "Equity", "weight": "0.65"},
            ]
        },
        "holdings": {"positionCount": "12"},
        "performance": {
            "summary": {"YTD": {"net_cumulative_return": "4.2"}},
            "benchmark": {
                "benchmark_code": "BMK_PB_GLOBAL_BALANCED_60_40",
                "comparison_status": "unavailable",
            },
        },
        "riskAnalytics": {
            "supportability": {"notes": []},
            "summary": {"YTD": {"volatility": "12", "drawdown": "-8", "value_at_risk": "-2"}},
        },
    }
    client_sections = [
        {"section_id": "executive_summary", "title": "Executive Review Summary", "status": "ready"},
        {
            "section_id": "performance_review",
            "title": "Performance Review",
            "status": "unavailable",
        },
    ]

    section = build_advisor_sections(
        portfolio_id="P1",
        as_of_date="2026-02-24",
        response=response,
        client_sections=client_sections,
    )[0]

    prompts = {item["prompt_id"]: item for item in section["items"]}
    assert prompts["review_readiness"]["prompt"] == (
        "Confirm report readiness is partial for P1 as of 2026-02-24 with unavailable client "
        "sections: Performance Review."
    )
    construction_prompt = prompts["portfolio_construction_review"]["prompt"]
    assert "USD 1000000.50" in construction_prompt
    assert "largest allocation Equity at 65.00%" in construction_prompt
    assert "12 sourced positions" in construction_prompt
    assert prompts["performance_discussion"]["prompt"] == (
        "Discuss performance using YTD net cumulative return 4.20%, benchmark "
        "BMK_PB_GLOBAL_BALANCED_60_40, benchmark comparison is not source-backed in this report, "
        "sub-year annualized returns are suppressed unless source support is explicit."
    )
    assert prompts["risk_discussion"]["prompt"] == (
        "Discuss YTD risk posture using volatility 12.00%, drawdown -8.00%, value at risk -2.00%."
    )
    assert all(item["source_refs"] for item in section["items"])


def test_advisor_sections_skip_prompts_when_requested_facts_are_not_source_backed():
    response = {
        "readiness": {},
        "evidence": {"source_refs": ["not-a-ref", {"section_id": "unrelated"}]},
        "overview": {"total_market_value": object(), "total_cash": "not-a-number"},
        "allocation": {"byAssetClass": []},
        "holdings": {"positionCount": "not-a-count"},
        "performance": {},
        "riskAnalytics": {},
    }
    client_sections = [
        {"section_id": "executive_summary", "title": "Executive Review Summary", "status": "ready"},
        {"section_id": "asset_allocation", "status": "omitted_by_request"},
    ]

    section = build_advisor_sections(
        portfolio_id="P1",
        as_of_date="2026-02-24",
        response=response,
        client_sections=client_sections,
    )[0]

    assert [item["prompt_id"] for item in section["items"]] == ["review_readiness"]
    readiness_prompt = section["items"][0]
    assert readiness_prompt["prompt"] == (
        "Confirm report readiness is ready for P1 as of 2026-02-24 with no unavailable client "
        "sections."
    )
    assert readiness_prompt["source_section_ids"] == ["executive_summary"]
    assert readiness_prompt["source_refs"] == []
    assert all(target["mutation_allowed"] is False for target in readiness_prompt["route_targets"])


def test_advisor_sections_handle_decimal_values_and_benchmark_only_performance():
    response = {
        "readiness": {"status": "ready"},
        "evidence": {"source_refs": "not-a-list"},
        "overview": {"total_market_value": Decimal("10.50"), "currency": "USD"},
        "performance": {
            "summary": {"YTD": {}},
            "benchmark": {"benchmark_code": "BMK_ONLY"},
        },
    }
    client_sections = [
        {"section_id": "executive_summary", "status": "ready"},
        {"section_id": "performance_review", "status": "ready"},
    ]

    section = build_advisor_sections(
        portfolio_id="P1",
        as_of_date="2026-02-24",
        response=response,
        client_sections=client_sections,
    )[0]

    prompts = {item["prompt_id"]: item for item in section["items"]}
    assert prompts["portfolio_construction_review"]["prompt"] == (
        "Discuss portfolio construction using total market value USD 10.50."
    )
    assert prompts["performance_discussion"]["prompt"] == (
        "Discuss performance using benchmark BMK_ONLY, sub-year annualized returns are suppressed "
        "unless source support is explicit."
    )
    assert prompts["performance_discussion"]["source_refs"] == []


def test_advisor_risk_prompt_explains_supportability_limitations_with_metrics():
    response = {
        "readiness": {"status": "partial"},
        "evidence": {"source_refs": []},
        "riskAnalytics": {
            "supportability": {
                "notes": [
                    {
                        "code": "missing_benchmark",
                        "severity": "warning",
                        "message": (
                            "Benchmark-relative risk posture is unavailable because benchmark "
                            "return series is not sourced for the risk calculation."
                        ),
                    }
                ]
            },
            "summary": {"YTD": {"volatility": "2.5", "drawdown": "-1.2", "value_at_risk": "-0.4"}},
        },
    }
    client_sections = [{"section_id": "risk_review", "title": "Risk Review", "status": "partial"}]

    section = build_advisor_sections(
        portfolio_id="P1",
        as_of_date="2026-02-24",
        response=response,
        client_sections=client_sections,
    )[0]

    prompts = {item["prompt_id"]: item for item in section["items"]}
    assert prompts["risk_discussion"]["prompt"] == (
        "Discuss YTD risk posture using volatility 2.50%, drawdown -1.20%, value at risk "
        "-0.40%. Supportability limitation: Benchmark-relative risk posture is unavailable "
        "because benchmark return series is not sourced for the risk calculation."
    )
