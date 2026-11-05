-- Isolated simulation schema. No existing v519 tables or policies are modified.
CREATE SCHEMA IF NOT EXISTS oneil_execution_v2;
REVOKE ALL ON SCHEMA oneil_execution_v2 FROM PUBLIC;
CREATE TABLE IF NOT EXISTS oneil_execution_v2.accounts (
 account text PRIMARY KEY CHECK (account ~ '^[0-9a-f]{64}$'),
 mode text NOT NULL DEFAULT 'SIMULATION' CHECK (mode = 'SIMULATION'),
 halted boolean NOT NULL DEFAULT true,
 revision bigint NOT NULL DEFAULT 1,
 policy jsonb NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS oneil_execution_v2.requests (
 request_id uuid PRIMARY KEY,
 account text NOT NULL REFERENCES oneil_execution_v2.accounts(account),
 idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 16 AND 128),
 payload jsonb NOT NULL,
 digest text NOT NULL CHECK (digest ~ '^[0-9a-f]{64}$'),
 approval_id text NOT NULL UNIQUE,
 principal text NOT NULL,
 approval_expires_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL,
 control_revision bigint NOT NULL,
 state text NOT NULL CHECK (state IN ('QUEUED','VALIDATING','SUBMISSION_UNKNOWN','ACKNOWLEDGED','PART_FILLED','FILLED','CANCELLED','REJECTED','EXPIRED','BLOCKED')),
 reason text NOT NULL,
 priority integer NOT NULL DEFAULT 0 CHECK (priority BETWEEN 0 AND 100),
 claim_token uuid,
 lease_until timestamptz,
 attempt_id uuid UNIQUE,
 trading_day date,
 admission_evidence jsonb,
 broker_order_id text,
 filled_quantity bigint NOT NULL DEFAULT 0 CHECK (filled_quantity >= 0),
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 1),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(account, idempotency_key),
 UNIQUE(account, trading_day, broker_order_id),
 CHECK (attempts=0 OR attempt_id IS NOT NULL),
 CHECK (state NOT IN ('SUBMISSION_UNKNOWN','ACKNOWLEDGED','PART_FILLED','FILLED') OR attempts=1),
 CHECK (filled_quantity <= (payload->>'quantity')::bigint)
);
ALTER TABLE oneil_execution_v2.requests ADD COLUMN IF NOT EXISTS admission_evidence jsonb;
ALTER TABLE oneil_execution_v2.requests DROP CONSTRAINT IF EXISTS policy_admission_required;
ALTER TABLE oneil_execution_v2.requests ADD CONSTRAINT policy_admission_required CHECK (
 (attempts > 0 OR admission_evidence IS NULL) AND
 (attempts=0 OR COALESCE(payload->>'policy_version' NOT LIKE 'policy-v2-%', true) OR
  COALESCE(jsonb_typeof(admission_evidence)='object'
    AND admission_evidence->>'order_digest'=digest
    AND admission_evidence->>'request_id'=request_id::text
    AND admission_evidence->'control_revision'=to_jsonb(control_revision)
    AND admission_evidence->>'mode'='SIMULATION'
    AND admission_evidence->'live_capable'='false'::jsonb, false))
);
CREATE INDEX IF NOT EXISTS execution_v2_queue ON oneil_execution_v2.requests(account,priority DESC,created_at) WHERE state IN ('QUEUED','VALIDATING');
CREATE TABLE IF NOT EXISTS oneil_execution_v2.events (
 sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 account text NOT NULL REFERENCES oneil_execution_v2.accounts(account),
 request_id uuid REFERENCES oneil_execution_v2.requests(request_id),
 event text NOT NULL,
 reason text NOT NULL,
 state text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS oneil_execution_v2.health (
 account text PRIMARY KEY REFERENCES oneil_execution_v2.accounts(account),
 worker_id text NOT NULL,
 heartbeat_at timestamptz NOT NULL,
 reconciled_at timestamptz,
 status text NOT NULL
);
-- Application operations only append audit rows. A future deployment needs separate
-- migration/runtime roles and DB grants denying audit UPDATE/DELETE; no public API.
REVOKE ALL ON ALL TABLES IN SCHEMA oneil_execution_v2 FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA oneil_execution_v2 FROM PUBLIC;
CREATE OR REPLACE FUNCTION oneil_execution_v2.guard_request_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.request_id,NEW.account,NEW.idempotency_key,NEW.payload,NEW.digest,NEW.approval_id,
        NEW.principal,NEW.approval_expires_at,NEW.expires_at,NEW.control_revision,NEW.created_at,NEW.priority)
    IS DISTINCT FROM ROW(OLD.request_id,OLD.account,OLD.idempotency_key,OLD.payload,OLD.digest,OLD.approval_id,
        OLD.principal,OLD.approval_expires_at,OLD.expires_at,OLD.control_revision,OLD.created_at,OLD.priority) THEN
   RAISE EXCEPTION 'immutable request content changed' USING ERRCODE='23514';
 END IF;
 IF (OLD.attempts=1 AND NEW.admission_evidence IS DISTINCT FROM OLD.admission_evidence)
    OR (NEW.attempts=0 AND NEW.admission_evidence IS NOT NULL) THEN
   RAISE EXCEPTION 'admission evidence fence changed' USING ERRCODE='23514';
 END IF;
 IF NEW.attempts < OLD.attempts OR (OLD.attempts=1 AND
     ROW(NEW.attempt_id,NEW.trading_day) IS DISTINCT FROM ROW(OLD.attempt_id,OLD.trading_day)) THEN
   RAISE EXCEPTION 'attempt fence changed' USING ERRCODE='23514';
 END IF;
 IF NEW.attempts > OLD.attempts AND NOT (OLD.state='VALIDATING' AND NEW.state='SUBMISSION_UNKNOWN' AND NEW.attempts=1) THEN
   RAISE EXCEPTION 'invalid admission' USING ERRCODE='23514';
 END IF;
 IF NEW.filled_quantity < OLD.filled_quantity OR (OLD.broker_order_id IS NOT NULL AND NEW.broker_order_id IS DISTINCT FROM OLD.broker_order_id) THEN
   RAISE EXCEPTION 'broker observation regressed' USING ERRCODE='23514';
 END IF;
 IF NEW.state <> OLD.state AND NOT (
   (OLD.state='QUEUED' AND NEW.state IN ('VALIDATING','CANCELLED')) OR
   (OLD.state='VALIDATING' AND NEW.state IN ('SUBMISSION_UNKNOWN','BLOCKED','EXPIRED','CANCELLED')) OR
   (OLD.state='SUBMISSION_UNKNOWN' AND NEW.state IN ('ACKNOWLEDGED','PART_FILLED','FILLED','CANCELLED','REJECTED')) OR
   (OLD.state='ACKNOWLEDGED' AND NEW.state IN ('PART_FILLED','FILLED','CANCELLED','REJECTED')) OR
   (OLD.state='PART_FILLED' AND NEW.state IN ('FILLED','CANCELLED'))
 ) THEN RAISE EXCEPTION 'invalid order state transition' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS guard_request_update ON oneil_execution_v2.requests;
CREATE TRIGGER guard_request_update BEFORE UPDATE ON oneil_execution_v2.requests
 FOR EACH ROW EXECUTE FUNCTION oneil_execution_v2.guard_request_update();
CREATE OR REPLACE FUNCTION oneil_execution_v2.guard_event_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'audit events are append-only' USING ERRCODE='23514'; END $$;
DROP TRIGGER IF EXISTS guard_event_mutation ON oneil_execution_v2.events;
CREATE TRIGGER guard_event_mutation BEFORE UPDATE OR DELETE ON oneil_execution_v2.events
 FOR EACH ROW EXECUTE FUNCTION oneil_execution_v2.guard_event_mutation();
