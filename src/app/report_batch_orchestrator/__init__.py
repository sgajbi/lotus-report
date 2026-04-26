"""Batch reporting orchestration boundary.

RFC-0104 will add durable batch scheduling and recovery here. Slice 1 creates
the module boundary only; no batch runtime or API is exported yet.
"""

from app.report_batch_orchestrator.contracts import (
    BATCH_CAPABILITY_KEY,
    BATCH_FREQUENCIES,
    BATCH_RUNTIME_SUPPORTED,
    BATCH_SELECTOR_MODES,
)

__all__ = [
    "BATCH_CAPABILITY_KEY",
    "BATCH_FREQUENCIES",
    "BATCH_RUNTIME_SUPPORTED",
    "BATCH_SELECTOR_MODES",
]
