CREATE TABLE IF NOT EXISTS report_job_work_item (
    work_item_id TEXT PRIMARY KEY,
    report_job_id TEXT NOT NULL UNIQUE REFERENCES report_job(report_job_id),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'leased', 'retry_pending', 'completed', 'failed')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    available_at TIMESTAMPTZ NOT NULL,
    lease_owner TEXT,
    lease_token TEXT,
    lease_acquired_at TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    last_error_category TEXT,
    last_error_summary TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    CHECK (
        (
            status = 'leased'
            AND lease_owner IS NOT NULL
            AND lease_token IS NOT NULL
            AND lease_acquired_at IS NOT NULL
            AND lease_expires_at IS NOT NULL
        )
        OR (
            status <> 'leased'
            AND lease_owner IS NULL
            AND lease_token IS NULL
            AND lease_acquired_at IS NULL
            AND lease_expires_at IS NULL
        )
    ),
    CHECK (
        (status = 'completed' AND completed_at IS NOT NULL)
        OR (status <> 'completed' AND completed_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_report_job_work_runnable
ON report_job_work_item(status, available_at, created_at)
WHERE status IN ('pending', 'retry_pending');

CREATE INDEX IF NOT EXISTS idx_report_job_work_lease_expiry
ON report_job_work_item(lease_expires_at)
WHERE status = 'leased';
