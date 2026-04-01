from __future__ import annotations

from app.config import (
    DEFAULT_PA_BASE_URL,
    DEFAULT_PAS_BASE_URL,
    DEFAULT_RISK_BASE_URL,
    Settings,
)


def test_settings_default_to_canonical_service_identities(monkeypatch) -> None:
    monkeypatch.delenv("PAS_BASE_URL", raising=False)
    monkeypatch.delenv("PA_BASE_URL", raising=False)
    monkeypatch.delenv("RISK_BASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.pas_base_url == DEFAULT_PAS_BASE_URL
    assert settings.pa_base_url == DEFAULT_PA_BASE_URL
    assert settings.risk_base_url == DEFAULT_RISK_BASE_URL
