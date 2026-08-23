import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_docker_compose_uses_host_reachable_upstreams_for_canonical_runtime() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "LOTUS_CORE_QUERY_BASE_URL: http://host.docker.internal:8201" in compose
    assert "LOTUS_PERFORMANCE_BASE_URL: http://host.docker.internal:8002" in compose
    assert "RISK_BASE_URL: http://host.docker.internal:8130" in compose
    assert "lotus-report-postgres:" in compose
    assert "image: postgres:16-alpine" in compose
    assert "REPORT_JOB_LEDGER_DATABASE_URL: postgresql://" in compose
    assert "condition: service_healthy" in compose
    assert '"host.docker.internal:host-gateway"' in compose
    assert "lotus-report-batch-worker:" in compose
    assert "lotus-report-job-worker:" in compose
    assert "exec python -m app.reporting_jobs.process" in compose
    assert "REPORT_JOB_WORKER_ID: lotus-report-job-worker-local" in compose
    assert "REPORT_JOB_WORKER_MAX_ITEMS_PER_PASS" in compose
    assert "python -m app.runtime_schema" in compose
    assert "exec python -m app.report_batch_orchestrator.process" in compose
    assert "REPORT_BATCH_WORKER_ID: lotus-report-batch-worker-local" in compose
    assert "REPORT_BATCH_WORKER_MAX_BATCHES_PER_PASS" in compose
    assert "REPORT_BATCH_WORKER_MAX_ACTIVE_ITEMS" in compose
    assert "lotus-report-batch-scheduler:" in compose
    assert "exec python -m app.report_batch_orchestrator.scheduler_process" in compose
    assert "REPORT_BATCH_SCHEDULER_ID: lotus-report-batch-scheduler-local" in compose
    assert "REPORT_BATCH_SCHEDULES_JSON:" in compose
    assert 'REPORT_BATCH_SCHEDULES_JSON: "[]"' in compose
    assert len(re.findall(r"^\s+image:\s+lotus-report", compose, flags=re.MULTILINE)) == 4
    assert "image: lotus-report:local" in compose
    assert "image: lotus-report-job-worker:local" in compose
    assert "image: lotus-report-batch-worker:local" in compose
    assert "image: lotus-report-batch-scheduler:local" in compose


def test_docker_image_copies_migrations_and_initializes_postgres_schema_before_api() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY migrations /app/migrations" in dockerfile
    assert "python -m app.runtime_schema" in dockerfile
    assert "exec uvicorn app.main:app --host 0.0.0.0 --port 8300" in dockerfile


def test_runtime_schema_guard_checks_ledger_and_snapshot_store() -> None:
    runtime_schema = (ROOT / "src" / "app" / "runtime_schema.py").read_text(encoding="utf-8")

    assert "pg_advisory_lock" in runtime_schema
    assert "pg_advisory_unlock" in runtime_schema
    assert "def ensure_runtime_schema() -> None:" in runtime_schema
    assert (
        "connection_provider = PostgresConnectionProvider.from_settings(settings)" in runtime_schema
    )
    assert "with _runtime_schema_lock(connection_provider):" in runtime_schema
    assert "PostgresReportBatchLedger(connection_provider=connection_provider)" in runtime_schema
    assert (
        "PostgresReportInputSnapshotStore(connection_provider=connection_provider)"
        in runtime_schema
    )
    assert "connection_provider.close()" in runtime_schema
    assert 'if __name__ == "__main__":' in runtime_schema
