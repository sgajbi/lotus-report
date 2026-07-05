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
    report_transaction_max_rows: int = Field(5000, ge=1, alias="REPORT_TRANSACTION_MAX_ROWS")
    report_transaction_max_pages: int = Field(20, ge=1, alias="REPORT_TRANSACTION_MAX_PAGES")
    report_job_ledger_database_url: str = Field(
        "postgresql://lotus_report:lotus_report@localhost:5439/lotus_report",
        alias="REPORT_JOB_LEDGER_DATABASE_URL",
    )
    batch_worker_id: str = Field(
        "lotus-report-batch-worker-1",
        alias="REPORT_BATCH_WORKER_ID",
    )
    batch_worker_interval_seconds: float = Field(
        5.0,
        ge=0.1,
        alias="REPORT_BATCH_WORKER_INTERVAL_SECONDS",
    )
    batch_worker_max_batches_per_pass: int = Field(
        5,
        ge=1,
        alias="REPORT_BATCH_WORKER_MAX_BATCHES_PER_PASS",
    )
    batch_worker_tenant_id: str = Field("tenant-sg", alias="REPORT_BATCH_WORKER_TENANT_ID")
    batch_worker_region: str = Field("APAC", alias="REPORT_BATCH_WORKER_REGION")
    batch_worker_booking_center_code: str | None = Field(
        "SG",
        alias="REPORT_BATCH_WORKER_BOOKING_CENTER_CODE",
    )
    batch_worker_role: str = Field("system", alias="REPORT_BATCH_WORKER_ROLE")
    batch_worker_max_active_batches: int = Field(
        1,
        ge=1,
        alias="REPORT_BATCH_WORKER_MAX_ACTIVE_BATCHES",
    )
    batch_worker_max_active_items: int = Field(
        5,
        ge=1,
        alias="REPORT_BATCH_WORKER_MAX_ACTIVE_ITEMS",
    )
    batch_worker_max_active_upstream_jobs: int = Field(
        3,
        ge=1,
        alias="REPORT_BATCH_WORKER_MAX_ACTIVE_UPSTREAM_JOBS",
    )
    batch_worker_max_active_render_jobs: int = Field(
        2,
        ge=1,
        alias="REPORT_BATCH_WORKER_MAX_ACTIVE_RENDER_JOBS",
    )
    batch_worker_max_active_archive_jobs: int = Field(
        2,
        ge=1,
        alias="REPORT_BATCH_WORKER_MAX_ACTIVE_ARCHIVE_JOBS",
    )
    batch_worker_lease_seconds: int = Field(
        300,
        ge=1,
        alias="REPORT_BATCH_WORKER_LEASE_SECONDS",
    )
    batch_scheduler_id: str = Field(
        "lotus-report-batch-scheduler-1",
        alias="REPORT_BATCH_SCHEDULER_ID",
    )
    batch_scheduler_interval_seconds: float = Field(
        60.0,
        ge=0.1,
        alias="REPORT_BATCH_SCHEDULER_INTERVAL_SECONDS",
    )
    batch_scheduler_tenant_id: str = Field("tenant-sg", alias="REPORT_BATCH_SCHEDULER_TENANT_ID")
    batch_scheduler_region: str = Field("APAC", alias="REPORT_BATCH_SCHEDULER_REGION")
    batch_scheduler_booking_center_code: str | None = Field(
        "SG",
        alias="REPORT_BATCH_SCHEDULER_BOOKING_CENTER_CODE",
    )
    batch_scheduler_role: str = Field("system", alias="REPORT_BATCH_SCHEDULER_ROLE")
    batch_schedules_json: str = Field("[]", alias="REPORT_BATCH_SCHEDULES_JSON")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
