-- Converge report_status_event column defaults across install paths (#376, PR #378 review).
--
-- Measured divergence (real PostgreSQL, migrations 000-026, fresh vs the
-- supported legacy baseline) is exactly TWO lingering column defaults:
--   event_schema_version  fresh 'report-status-event.v1'  vs legacy 'report-status-event.legacy.v0'
--   event_payload_json    fresh '{}'                      vs legacy '{"payload_posture":"legacy_message_only"}'
-- The legacy values came from 000/009's ADD COLUMN backfill of populated
-- pre-contract tables. The backfilled ROW values are correct and untouched.
-- event_family's default ('job_lifecycle') is IDENTICAL on both paths and did
-- not diverge. It is dropped here anyway for one uniform posture, because a
-- surviving default lets a future INSERT that omits the column silently stamp
-- an invented value — on the divergent two, a fabricated legacy marker or an
-- empty payload. Both shipped writers name every contract column explicitly
-- (reporting_jobs/ledger.py, reporting_jobs/postgres_ledger.py), so no
-- default is load-bearing.
--
-- Dropping the defaults converges every install path to one schema and makes
-- an INSERT that omits a contract column fail closed instead of inventing a
-- value. DROP DEFAULT is a no-op when no default exists, and under the
-- applied-migration ledger this file executes once per database.

ALTER TABLE report_status_event
ALTER COLUMN event_schema_version DROP DEFAULT;

ALTER TABLE report_status_event
ALTER COLUMN event_family DROP DEFAULT;

ALTER TABLE report_status_event
ALTER COLUMN event_payload_json DROP DEFAULT;
