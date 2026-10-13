-- Inactive by default: only an explicitly injected server admission sets custody_policy.
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS custody_policy TEXT
    CHECK(custody_policy IS NULL OR custody_policy='retained-render-offer.v1');
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS render_offer_body BYTEA;
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS render_offer_sha256 TEXT;
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS render_offer_size INTEGER;
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS render_offer_version TEXT;
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS render_offer_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS custody_state TEXT
    CHECK(custody_state IS NULL OR custody_state IN ('ready','waiting','blocked','refused','verified'));
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS custody_epoch INTEGER NOT NULL DEFAULT 0;
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS custody_initial_count INTEGER NOT NULL DEFAULT 0
    CHECK(custody_initial_count BETWEEN 0 AND 1);
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS custody_dispatch_count INTEGER NOT NULL DEFAULT 0
    CHECK(custody_dispatch_count BETWEEN 0 AND 3);
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS custody_poll_count INTEGER NOT NULL DEFAULT 0
    CHECK(custody_poll_count BETWEEN 0 AND 12);
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS custody_window_started_at TIMESTAMPTZ;
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS custody_next_attempt_at TIMESTAMPTZ;
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS custody_reason TEXT;
ALTER TABLE report_job ADD COLUMN IF NOT EXISTS custody_verification_json TEXT;

CREATE OR REPLACE FUNCTION protect_report_custody() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.render_offer_body IS NOT NULL OR NEW.custody_state IS NOT NULL
           OR NEW.custody_initial_count <> 0 OR NEW.custody_dispatch_count <> 0
           OR NEW.custody_poll_count <> 0 OR NEW.render_offer_generation <> 0 THEN
            RAISE EXCEPTION 'custody_insert_shape';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.custody_policy IS DISTINCT FROM OLD.custody_policy
       OR (OLD.render_offer_body IS NOT NULL AND (
           NEW.render_offer_body IS DISTINCT FROM OLD.render_offer_body
           OR NEW.render_offer_sha256 IS DISTINCT FROM OLD.render_offer_sha256
           OR NEW.render_offer_size IS DISTINCT FROM OLD.render_offer_size
           OR NEW.render_offer_version IS DISTINCT FROM OLD.render_offer_version
           OR NEW.render_offer_generation IS DISTINCT FROM OLD.render_offer_generation
           OR NEW.render_job_id IS DISTINCT FROM OLD.render_job_id
           OR NEW.report_request_id IS DISTINCT FROM OLD.report_request_id
           OR NEW.report_type IS DISTINCT FROM OLD.report_type
           OR NEW.render_template_id IS DISTINCT FROM OLD.render_template_id
           OR NEW.render_template_version IS DISTINCT FROM OLD.render_template_version
           OR NEW.accepted_document_contract_json IS DISTINCT FROM OLD.accepted_document_contract_json))
       OR (OLD.custody_policy = 'retained-render-offer.v1'
           AND OLD.archive_request_id IS NOT NULL
           AND NEW.archive_request_id IS DISTINCT FROM OLD.archive_request_id)
       OR (OLD.custody_state = 'verified' AND NEW.custody_state IS DISTINCT FROM 'verified')
       OR (OLD.custody_state = 'verified' AND (
           NEW.status IS DISTINCT FROM 'archived'
           OR NEW.archive_document_id IS DISTINCT FROM OLD.archive_document_id
           OR NEW.custody_verification_json IS DISTINCT FROM OLD.custody_verification_json)) THEN
        RAISE EXCEPTION 'custody_immutable';
    END IF;
    IF (NEW.render_offer_body IS NOT NULL AND (
        NEW.custody_policy IS DISTINCT FROM 'retained-render-offer.v1'
        OR NEW.render_offer_size IS DISTINCT FROM octet_length(NEW.render_offer_body)
        OR NEW.render_offer_size NOT BETWEEN 1 AND 8388608
        OR NEW.render_offer_sha256 IS NULL OR length(NEW.render_offer_sha256) <> 64
        OR NEW.render_offer_sha256 !~ '^[0-9a-f]{64}$'
        OR NEW.render_offer_version IS DISTINCT FROM 'json-utf8-compact.v1'
        OR NEW.render_offer_generation <> 1 OR NEW.render_job_id IS NULL))
       OR (NEW.custody_policy = 'retained-render-offer.v1'
           AND NEW.custody_state IS DISTINCT FROM 'verified'
           AND (OLD.render_offer_sha256 IS NOT NULL OR OLD.render_job_id IS NOT NULL
                OR OLD.custody_initial_count > 0
                OR OLD.status IN ('rendering', 'completed', 'archiving'))
           AND NEW.status IN ('failed', 'cancelled', 'archived', 'completed',
                              'completed_with_warnings'))
       OR NEW.custody_initial_count < OLD.custody_initial_count
       OR (NEW.custody_state = 'verified' AND (NEW.custody_verification_json IS NULL
           OR NEW.archive_request_id IS NULL OR NEW.archive_document_id IS NULL
           OR OLD.render_offer_sha256 IS NULL)) THEN
        RAISE EXCEPTION 'custody_shape';
    END IF;
    RETURN NEW;
END;
$$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                   WHERE tgname='report_custody_guard' AND tgrelid='report_job'::regclass) THEN
        CREATE TRIGGER report_custody_guard BEFORE INSERT OR UPDATE ON report_job
            FOR EACH ROW EXECUTE FUNCTION protect_report_custody();
    END IF;
END $$;
