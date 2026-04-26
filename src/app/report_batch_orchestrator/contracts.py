"""RFC-0104 batch reporting vocabulary and support posture.

This module centralizes future batch-orchestration vocabulary without exposing
runtime behavior before the durable ledger, scheduler, worker, and APIs exist.
"""

from typing import Final, Literal

BatchSelectorMode = Literal[
    "explicit_portfolio_list",
    "selected_subset",
    "all_active_portfolios",
    "batch_manifest",
]

BatchFrequency = Literal[
    "monthly",
    "quarterly",
    "semi_annual",
    "yearly",
    "explicit",
]

BATCH_CAPABILITY_KEY: Final = "lotus-report.reporting.batch_orchestration.v1"
BATCH_MATERIALIZATION_API_CAPABILITY_KEY: Final = (
    "lotus-report.reporting.batch_materialization_api.v1"
)
BATCH_CONTROL_API_CAPABILITY_KEY: Final = "lotus-report.reporting.batch_control_api.v1"

BATCH_SELECTOR_MODES: Final[tuple[BatchSelectorMode, ...]] = (
    "explicit_portfolio_list",
    "selected_subset",
    "all_active_portfolios",
    "batch_manifest",
)

BATCH_FREQUENCIES: Final[tuple[BatchFrequency, ...]] = (
    "monthly",
    "quarterly",
    "semi_annual",
    "yearly",
    "explicit",
)

BATCH_RUNTIME_SUPPORTED: Final = False
