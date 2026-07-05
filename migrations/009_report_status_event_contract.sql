ALTER TABLE report_status_event
ADD COLUMN IF NOT EXISTS event_schema_version TEXT NOT NULL DEFAULT 'report-status-event.legacy.v0';

ALTER TABLE report_status_event
ADD COLUMN IF NOT EXISTS event_family TEXT NOT NULL DEFAULT 'job_lifecycle';

ALTER TABLE report_status_event
ADD COLUMN IF NOT EXISTS event_payload_json JSONB NOT NULL DEFAULT '{"payload_posture":"legacy_message_only"}'::jsonb;

ALTER TABLE report_status_event
ADD COLUMN IF NOT EXISTS event_idempotency_key TEXT;

CREATE INDEX IF NOT EXISTS idx_report_status_event_family_created
ON report_status_event(event_family, created_at);

CREATE INDEX IF NOT EXISTS idx_report_status_event_idempotency_key
ON report_status_event(event_idempotency_key)
WHERE event_idempotency_key IS NOT NULL;
