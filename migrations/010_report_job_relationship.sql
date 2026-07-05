CREATE TABLE IF NOT EXISTS report_job_relationship (
    relationship_id TEXT PRIMARY KEY,
    source_report_job_id TEXT NOT NULL REFERENCES report_job(report_job_id),
    derived_report_job_id TEXT NOT NULL REFERENCES report_job(report_job_id),
    relationship_type TEXT NOT NULL CHECK (
        relationship_type IN (
            'regenerate_replacement',
            'failed_work_replay',
            'batch_item_replay'
        )
    ),
    source_status TEXT NOT NULL,
    derived_status TEXT NOT NULL,
    source_failure_category TEXT,
    derived_failure_category TEXT,
    archive_consequence TEXT,
    previous_archive_document_id TEXT,
    new_archive_document_id TEXT,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE(source_report_job_id, derived_report_job_id, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_report_job_relationship_source_created
ON report_job_relationship(source_report_job_id, created_at);

CREATE INDEX IF NOT EXISTS idx_report_job_relationship_derived_created
ON report_job_relationship(derived_report_job_id, created_at);
