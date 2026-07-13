ALTER TABLE IF EXISTS report_status_event
ADD COLUMN IF NOT EXISTS event_schema_version TEXT NOT NULL DEFAULT 'report-status-event.legacy.v0';

ALTER TABLE IF EXISTS report_status_event
ADD COLUMN IF NOT EXISTS event_family TEXT NOT NULL DEFAULT 'job_lifecycle';

ALTER TABLE IF EXISTS report_status_event
ADD COLUMN IF NOT EXISTS event_payload_json JSONB NOT NULL DEFAULT '{"payload_posture":"legacy_message_only"}'::jsonb;

ALTER TABLE IF EXISTS report_status_event
ADD COLUMN IF NOT EXISTS event_idempotency_key TEXT;
