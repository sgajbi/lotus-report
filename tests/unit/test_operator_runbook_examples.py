from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _code_blocks(text: str) -> list[str]:
    return re.findall(r"```[a-zA-Z]*\n(.*?)```", text, flags=re.DOTALL)


def _block_for(text: str, endpoint_fragment: str) -> str:
    for block in _code_blocks(text):
        if endpoint_fragment in block:
            return block
    msg = f"Missing copy-paste example for {endpoint_fragment}"
    raise AssertionError(msg)


def test_operator_header_config_documents_required_caller_context() -> None:
    for relative_path in ("wiki/API-Surface.md", "wiki/Operations-Runbook.md"):
        text = _read(relative_path)
        config_block = _block_for(text, "report-operator-headers.curl")

        for header in (
            "X-Actor-Id",
            "X-Caller-Application",
            "X-Tenant-Id",
            "X-Region",
            "X-Booking-Center-Code",
            "X-Role",
            "X-Correlation-ID",
            "X-Trace-ID",
        ):
            assert header in config_block


def test_api_surface_examples_use_caller_context_config() -> None:
    api_surface = _read("wiki/API-Surface.md")
    endpoints = (
        "/api/v1/reports/portfolio-reviews",
        "/api/v1/report-jobs/rjob_example",
        "/reports/jobs/rjob_example/snapshot",
        "/reports/jobs/rjob_example/lineage",
        "/reports/jobs/rjob_example/portfolio-memory-events",
        "/api/v1/report-jobs?tenantId=tenant-sg",
        "/api/v1/report-jobs/rjob_example/cancel",
        "/reports/jobs/rjob_example/rerender",
        "/reports/jobs/rjob_example/regenerate",
        "/reports/jobs/rjob_failed_example/replay",
        "/reports/batches",
        "/reports/batches/rbatch_example",
        "/reports/batches/rbatch_example:pause",
        "/reports/batches/rbatch_example:resume",
        "/reports/batches/rbatch_example:cancel",
        "/reports/batches/rbatch_example:retry-failed",
        "/reports/batches/rbatch_example:recover-expired-leases",
        "/reports/batches/rbatch_example/items/rbci_failed_example/replay",
        "/reports/batches/rbatch_example:run-once",
    )

    for endpoint in endpoints:
        block = _block_for(api_surface, endpoint)
        assert "--config report-operator-headers.curl" in block


def test_mutation_examples_document_required_idempotency_keys() -> None:
    combined = "\n".join([_read("wiki/API-Surface.md"), _read("wiki/Operations-Runbook.md")])
    idempotent_mutations = {
        "/api/v1/reports/portfolio-reviews": "portfolio-review-PB_SG_GLOBAL_BAL_001-2026-04-22",
        "/reports/jobs/rjob_example/rerender": "rerender-rjob_example-v1",
        "/reports/jobs/rjob_example/regenerate": "regenerate-rjob_example-v1",
        "/reports/jobs/rjob_failed_example/replay": "replay-rjob_failed_example-v1",
        "/reports/batches": "batch-PB_SG_GLOBAL_BAL_001-2026-04-22",
        "/reports/batches/rbatch_example/items/rbci_failed_example/replay": (
            "batch-item-replay-rbci_failed_example-v1"
        ),
    }

    for endpoint, idempotency_key in idempotent_mutations.items():
        block = _block_for(combined, endpoint)
        assert "Idempotency-Key" in block
        assert idempotency_key in block


def test_operations_runbook_covers_support_safe_job_and_batch_commands() -> None:
    runbook = _read("wiki/Operations-Runbook.md")
    support_commands = (
        "/api/v1/report-jobs/rjob_example",
        "/api/v1/report-jobs/rjob_example/events",
        "/reports/jobs/rjob_example/diagnostics",
        "/reports/jobs/rjob_example/snapshot",
        "/reports/jobs/rjob_example/lineage",
        "/api/v1/report-jobs/rjob_example/cancel",
        "/reports/batches/rbatch_example",
        "/reports/batches/rbatch_example:pause",
        "/reports/batches/rbatch_example:resume",
        "/reports/batches/rbatch_example:cancel",
        "/reports/batches/rbatch_example:retry-failed",
        "/reports/batches/rbatch_example:recover-expired-leases",
        "/reports/batches/rbatch_example:run-once",
    )

    for endpoint in support_commands:
        block = _block_for(runbook, endpoint)
        assert "--config report-operator-headers.curl" in block


def test_validation_wiki_matches_the_enforced_coverage_floor() -> None:
    makefile = _read("Makefile")
    validation_wiki = _read("wiki/Validation-and-CI.md")
    match = re.search(r"^COVERAGE_FAIL_UNDER \?= (?P<floor>\d+)$", makefile, re.MULTILINE)

    assert match is not None
    assert f"{match.group('floor')}% coverage floor" in validation_wiki


def test_report_job_docs_keep_acceptance_separate_from_completion() -> None:
    api_surface = _read("wiki/API-Surface.md")
    operations = _read("wiki/Operations-Runbook.md")
    validation = _read("wiki/Validation-and-CI.md")
    supported_features = _read("docs/supported-features.md")

    assert "`202 Accepted` does not mean the report is complete" in api_surface
    assert "Acceptance means the request, job" in operations
    assert "it does not mean source capture" in operations
    assert "lotus-report-job-worker" in operations
    assert "lotus_report_job_runtime_last_items" in operations
    assert "lotus-report-job-worker" in validation
    assert "`report_job_work_item`" in validation
    assert "portfolio_review.durable_async_execution.v1" in supported_features


def test_snapshot_lineage_docs_require_atomic_complete_resume() -> None:
    durability = _read("docs/standards/durability-consistency.md")
    operations = _read("wiki/Operations-Runbook.md")
    context = _read("REPOSITORY-ENGINEERING-CONTEXT.md")
    supported_features = _read("docs/supported-features.md")

    for text in (durability, operations, context, supported_features):
        assert "data_ready" in text
        assert "data_incomplete" in text
        assert "upstream-call" in text

    assert "one transaction" in durability
    assert "declared call count is positive" in durability
    assert "snapshot and upstream-call rows commit as one capture transaction" in operations
    assert "snapshot presence alone" in durability
    assert "same-payload zero-call historical gap" in supported_features
