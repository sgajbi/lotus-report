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
