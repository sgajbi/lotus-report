from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_LOTUS_CORE_QUERY_BASE_URL = "http://core-query.dev.lotus"
DEFAULT_LOTUS_PERFORMANCE_BASE_URL = "http://performance.dev.lotus"
DEFAULT_RISK_BASE_URL = "http://risk.dev.lotus"
DEFAULT_LOTUS_RENDER_BASE_URL = "http://render.dev.lotus"
DEFAULT_LOTUS_ARCHIVE_BASE_URL = "http://archive.dev.lotus"


class Settings(BaseSettings):
    contract_version: str = Field("v1", alias="CONTRACT_VERSION")
    core_query_base_url: str = Field(
        DEFAULT_LOTUS_CORE_QUERY_BASE_URL,
        alias="LOTUS_CORE_QUERY_BASE_URL",
    )
    performance_base_url: str = Field(
        DEFAULT_LOTUS_PERFORMANCE_BASE_URL,
        alias="LOTUS_PERFORMANCE_BASE_URL",
    )
    risk_base_url: str = Field(DEFAULT_RISK_BASE_URL, alias="RISK_BASE_URL")
    render_base_url: str = Field(DEFAULT_LOTUS_RENDER_BASE_URL, alias="LOTUS_RENDER_BASE_URL")
    archive_base_url: str = Field(DEFAULT_LOTUS_ARCHIVE_BASE_URL, alias="LOTUS_ARCHIVE_BASE_URL")
    upstream_timeout_seconds: float = Field(10.0, alias="UPSTREAM_TIMEOUT_SECONDS")
    upstream_max_retries: int = Field(2, alias="UPSTREAM_MAX_RETRIES")
    upstream_retry_backoff_seconds: float = Field(0.2, alias="UPSTREAM_RETRY_BACKOFF_SECONDS")
    report_job_ledger_database_url: str = Field(
        "postgresql://lotus_report:lotus_report@localhost:5439/lotus_report",
        alias="REPORT_JOB_LEDGER_DATABASE_URL",
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
