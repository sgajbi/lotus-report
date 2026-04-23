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
