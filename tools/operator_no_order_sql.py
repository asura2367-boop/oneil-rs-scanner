"""Offline SQL generation for a separately authorized private operator channel.
Never connects to a DB. Never execute generated SQL without explicit review.
"""
import hashlib
import json
import re
from v519_no_order_review import prepare_review
from v519_pg_ledger import LedgerBlocked


def sql_for_review(intent, review, source_bytes, *, now, env, schema='public'):
    if any(env.get(k, '').strip().lower() != 'false' for k in ('LIVE_ENABLED','KIS_ALLOW_ORDER')):
        raise LedgerBlocked('EXPLICIT_DISABLED_SWITCHES_REQUIRED')
    if not isinstance(source_bytes, bytes) or hashlib.sha256(source_bytes).hexdigest() != review.get('source_evidence_sha256'):
        raise LedgerBlocked('SOURCE_EVIDENCE_DIGEST_MISMATCH')
    checked = prepare_review(intent, review, now=now)
    if schema != 'public' and not re.fullmatch('no_order_test_[0-9a-f]{32}',schema):
        raise LedgerBlocked('SCHEMA_NOT_ALLOWED')
    # All interpolated strings are validated identifiers/hex/date, not raw user text.
    # JSON is hex-encoded to prevent SQL and dollar-quote delimiter injection.
    # No source evidence bytes are sent.
    minimal = {k: review[k] for k in ('intent_id','account_fingerprint','reviewer','reviewed_at')}
    canonical = json.dumps(minimal,sort_keys=True,separators=(',',':'),allow_nan=False)
    literal = canonical.encode().hex()
    key = int(intent['account_fingerprint'][:16],16) % (2**63)
    # Use xact advisory lock: same PostgreSQL lock namespace as the runtime's
    # session advisory lock, but automatically released on commit/rollback.
    sql = f"""DO $v519_operator_review$
DECLARE
    h jsonb := convert_from(decode('{literal}','hex'),'UTF8')::jsonb;
    current_intent record;
    control_row record;
    server_now timestamptz;
    reviewed timestamptz;
    affected integer;
BEGIN
    PERFORM set_config('lock_timeout','5000',true);
    IF NOT pg_try_advisory_xact_lock({key}::bigint) THEN
        RAISE EXCEPTION 'ACCOUNT_RUNTIME_ALREADY_RUNNING';
    END IF;
    SELECT halted INTO control_row FROM {schema}.v519_control
        WHERE singleton=true FOR UPDATE;
    IF NOT FOUND OR control_row.halted IS DISTINCT FROM true THEN
        RAISE EXCEPTION 'EMERGENCY_HALT_REQUIRED';
    END IF;
    SELECT * INTO current_intent FROM {schema}.v519_order_intents
        WHERE intent_id=h->>'intent_id' AND account_fingerprint=h->>'account_fingerprint'
        FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'INTENT_NOT_FOUND'; END IF;
    server_now := clock_timestamp();
    IF current_intent.status IS DISTINCT FROM 'SUBMISSION_UNKNOWN'
       OR current_intent.side IS DISTINCT FROM 'BUY'
       OR current_intent.broker_order_id IS NOT NULL
       OR current_intent.filled_quantity IS DISTINCT FROM 0
       OR current_intent.verified_not_sent IS DISTINCT FROM false
       OR current_intent.trading_day IS DISTINCT FROM DATE '{intent['trading_day'].isoformat()}'
       OR current_intent.ticker IS DISTINCT FROM '{intent['ticker']}' THEN
        RAISE EXCEPTION 'NO_ORDER_REVIEW_STATE_CONFLICT';
    END IF;
    IF current_intent.trading_day >= (server_now AT TIME ZONE 'Asia/Seoul')::date THEN
        RAISE EXCEPTION 'COMPLETED_DAY_AND_IDENTITY_REQUIRED';
    END IF;
    reviewed := (h->>'reviewed_at')::timestamptz;
    IF reviewed > server_now OR server_now-reviewed > interval '900 seconds'
       OR (reviewed AT TIME ZONE 'Asia/Seoul')::date <= current_intent.trading_day THEN
        RAISE EXCEPTION 'REVIEW_STALE_OR_INVALID';
    END IF;
    INSERT INTO {schema}.v519_no_order_reviews
        (intent_id,account_fingerprint,evidence_sha256,reviewer,reviewed_at,resolution)
        VALUES (h->>'intent_id',h->>'account_fingerprint','{checked['evidence_sha256']}',
                h->>'reviewer',reviewed,'OPERATOR_VERIFIED_NO_ORDER');
    UPDATE {schema}.v519_order_intents SET status='REJECTED',updated_at=now()
        WHERE intent_id=h->>'intent_id' AND account_fingerprint=h->>'account_fingerprint';
    GET DIAGNOSTICS affected = ROW_COUNT;
    IF affected <> 1 THEN RAISE EXCEPTION 'NO_ORDER_RESOLUTION_NOT_APPLIED'; END IF;
END;
$v519_operator_review$;
"""
    return {'sql':sql,'sql_sha256':hashlib.sha256(sql.encode()).hexdigest(),
            'evidence_sha256':checked['evidence_sha256']}
