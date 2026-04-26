from __future__ import annotations

from app.config import (
    DEFAULT_LOTUS_CORE_QUERY_BASE_URL,
    DEFAULT_LOTUS_PERFORMANCE_BASE_URL,
    DEFAULT_RISK_BASE_URL,
    Settings,
)


def test_settings_default_to_canonical_service_identities(monkeypatch) -> None:
    monkeypatch.delenv("LOTUS_CORE_QUERY_BASE_URL", raising=False)
    monkeypatch.delenv("LOTUS_PERFORMANCE_BASE_URL", raising=False)
    monkeypatch.delenv("RISK_BASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.core_query_base_url == DEFAULT_LOTUS_CORE_QUERY_BASE_URL
    assert settings.performance_base_url == DEFAULT_LOTUS_PERFORMANCE_BASE_URL
    assert settings.risk_base_url == DEFAULT_RISK_BASE_URL
    assert settings.batch_worker_id == "lotus-report-batch-worker-1"
    assert settings.batch_worker_interval_seconds == 5.0
    assert settings.batch_worker_max_batches_per_pass == 5
    assert settings.batch_worker_tenant_id == "tenant-sg"
    assert settings.batch_worker_region == "APAC"
    assert settings.batch_worker_booking_center_code == "SG"
    assert settings.batch_worker_role == "system"
    assert settings.batch_worker_max_active_batches == 1
    assert settings.batch_worker_max_active_items == 5
    assert settings.batch_scheduler_id == "lotus-report-batch-scheduler-1"
    assert settings.batch_scheduler_interval_seconds == 60.0
    assert settings.batch_scheduler_tenant_id == "tenant-sg"
    assert settings.batch_scheduler_region == "APAC"
    assert settings.batch_scheduler_booking_center_code == "SG"
    assert settings.batch_scheduler_role == "system"
    assert settings.batch_schedules_json == "[]"
