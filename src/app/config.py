from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PAS_BASE_URL = "http://core-query.dev.lotus"
DEFAULT_PA_BASE_URL = "http://performance.dev.lotus"
DEFAULT_RISK_BASE_URL = "http://risk.dev.lotus"


class Settings(BaseSettings):
    contract_version: str = Field("v1", alias="CONTRACT_VERSION")
    pas_base_url: str = Field(DEFAULT_PAS_BASE_URL, alias="PAS_BASE_URL")
    pa_base_url: str = Field(DEFAULT_PA_BASE_URL, alias="PA_BASE_URL")
    risk_base_url: str = Field(DEFAULT_RISK_BASE_URL, alias="RISK_BASE_URL")
    upstream_timeout_seconds: float = Field(10.0, alias="UPSTREAM_TIMEOUT_SECONDS")
    upstream_max_retries: int = Field(2, alias="UPSTREAM_MAX_RETRIES")
    upstream_retry_backoff_seconds: float = Field(0.2, alias="UPSTREAM_RETRY_BACKOFF_SECONDS")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
