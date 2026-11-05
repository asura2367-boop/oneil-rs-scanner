"""Offline, operator-reviewed proof of one pinned synchronous pre-POST failure.
Not broker-history reconciliation. Never connects, retries or activates trading.
"""
import datetime as dt
import hashlib
import json
import re
from zoneinfo import ZoneInfo
from v519_pg_ledger import LedgerBlocked

SOURCE_COMMIT='abf446b8af195684f5d3884e72a09706081a379e'
ENGINE_BLOB='f9dc1f6ce4329542a64de6071a81fa7b5f1b52da'
WORKFLOW_BLOB='8637f74f52a53acfed79b28027f5893bb774474c'
WORKFLOW_RUN=41975997898
WORKFLOW_JOB=127075193914
CODE='FRESH_EXECUTABLE_BOOK_REQUIRED'
FRAMES=[{'file':'oneil_v519_live_engine.py','function':'_automatic_cycle','line':910},
        {'file':'oneil_v519_live_engine.py','function':'submit','line':547}]
TOP_FRAMES=[{'file':'oneil_v519_live_engine.py','function':'runtime_main','line':1560},
            {'file':'oneil_v519_live_engine.py','function':'integrated_cycle','line':1154},
            {'file':'oneil_v519_live_engine.py','function':'_automatic_cycle','line':934}]
KST=ZoneInfo('Asia/Seoul')
AUDIT_SCHEMA='''CREATE TABLE IF NOT EXISTS v519_pre_send_reviews (
 intent_id text PRIMARY KEY REFERENCES v519_order_intents(intent_id),
 account_fingerprint text NOT NULL,
 ledger_run_id text NOT NULL REFERENCES v519_runs(run_id),
 source_commit text NOT NULL,
 engine_blob text NOT NULL,
 workflow_run_id bigint NOT NULL,
 workflow_job_id bigint NOT NULL,
 log_sha256 text NOT NULL CHECK(log_sha256 ~ '^[0-9a-f]{64}$'),
 evidence_sha256 text NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 reviewer text NOT NULL,
 reviewed_at timestamptz NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT now(),
 resolution text NOT NULL CHECK(resolution='OPERATOR_VERIFIED_PRE_SEND_ABORT')
);
ALTER TABLE v519_pre_send_reviews ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON v519_pre_send_reviews FROM PUBLIC;
DO $$ DECLARE role_name text; BEGIN
 FOR role_name IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated') LOOP
  EXECUTE format('REVOKE ALL ON v519_pre_send_reviews FROM %I',role_name);
 END LOOP;
END $$;
'''


def blob_sha(data):
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()


def aware(value):
    if not isinstance(value,dt.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LedgerBlocked('AWARE_CLOCK_REQUIRED')
    return value


def exact_failure(value):
    return (isinstance(value,dict) and value.get('status')=='BLOCKED'
            and value.get('gate')=='DENY' and value.get('orders_sent','missing') is None
            and type(value.get('order_attempts')) is int and value['order_attempts']==1
            and type(value.get('confirmed_orders_sent')) is int and value['confirmed_orders_sent']==0
            and value.get('submission_stage')=='SUBMIT'
            and value.get('failure_kind')=='INVALID_VALUE'
            and value.get('failure_frames')==TOP_FRAMES
            and value.get('submission_failure')=={'failure_kind':'INVALID_VALUE',
                'failure_code':CODE,'failure_frames':FRAMES})


def prepare_pre_send_review(intent,run,review,*,engine_source,workflow_source,job_log,source_evidence,now,env):
    aware(now)
    if any(env.get(k,'').lower()!='false' for k in ('LIVE_ENABLED','KIS_ALLOW_ORDER')):
        raise LedgerBlocked('EXPLICIT_DISABLED_SWITCHES_REQUIRED')
    if (blob_sha(engine_source)!=ENGINE_BLOB or blob_sha(workflow_source)!=WORKFLOW_BLOB
            or review.get('source_commit')!=SOURCE_COMMIT
            or review.get('workflow_run_id')!=WORKFLOW_RUN or review.get('workflow_job_id')!=WORKFLOW_JOB):
        raise LedgerBlocked('PINNED_SOURCE_PROVENANCE_REQUIRED')
    # Pinning the complete source makes line provenance explicit. Never classify
    # arbitrary network exceptions by matching an error message alone.
    lines=engine_source.decode().splitlines()
    if ("raise TradingBlocked('"+CODE+"')" not in lines[546]
            or 'return self._send_verified(plan)' not in lines[549]
            or 'post=True' not in lines[565]):
        raise LedgerBlocked('PRE_SEND_CONTROL_FLOW_NOT_PROVEN')
    if any(review.get(k) is not True for k in ('github_origin_verified','checkout_unchanged_verified',
              'unique_run_binding_verified','operator_approved')):
        raise LedgerBlocked('TRUSTED_OPERATOR_PROVENANCE_REQUIRED')
    if (review.get('source_evidence_sha256')!=hashlib.sha256(source_evidence).hexdigest()
            or review.get('log_sha256')!=hashlib.sha256(job_log).hexdigest()):
        raise LedgerBlocked('SOURCE_EVIDENCE_DIGEST_MISMATCH')
    fp=intent.get('account_fingerprint');day=intent.get('trading_day');ident=intent.get('intent_id')
    if (not isinstance(fp,str) or not re.fullmatch('[0-9a-f]{64}',fp)
            or type(day) is not dt.date or day>now.astimezone(KST).date()
            or not isinstance(intent.get('ticker'),str) or not re.fullmatch('[0-9]{6}',intent['ticker'])):
        raise LedgerBlocked('INTENT_IDENTITY_INVALID')
    expected=hashlib.sha256(f"v519|{fp}|{day}|{intent['ticker']}|BUY".encode()).hexdigest()
    if (ident!=expected or intent.get('side')!='BUY' or intent.get('status')!='SUBMISSION_UNKNOWN'
            or intent.get('broker_order_id') is not None or type(intent.get('filled_quantity')) is not int
            or intent['filled_quantity']!=0 or intent.get('verified_not_sent') is not False):
        raise LedgerBlocked('PRE_SEND_INTENT_STATE_CONFLICT')
    if (run.get('account_fingerprint')!=fp or review.get('account_fingerprint')!=fp
            or review.get('intent_id')!=ident or review.get('ledger_run_id')!=run.get('run_id')
            or not re.fullmatch('[0-9a-f]{32}',str(run.get('run_id')))):
        raise LedgerBlocked('RUN_IDENTITY_MISMATCH')
    started=aware(run.get('started_at'));finished=aware(run.get('finished_at'));created=aware(intent.get('created_at'))
    if not started<=created<=finished<=now or started.astimezone(KST).date()!=day:
        raise LedgerBlocked('RUN_TIME_BINDING_INVALID')
    if not exact_failure(run.get('result')):raise LedgerBlocked('EXACT_PRE_SEND_FAILURE_REQUIRED')
    if review.get('matching_run_count')!=1 or type(review['matching_run_count']) is not int or review.get('matching_intent_count')!=1 or type(review['matching_intent_count']) is not int:
        raise LedgerBlocked('AMBIGUOUS_RUN_BINDING')
    records=[]
    text=job_log.decode('utf-8-sig')
    if SOURCE_COMMIT not in text:raise LedgerBlocked('CHECKOUT_COMMIT_MISSING')
    for line in text.splitlines():
        try:
            stamp,payload=line.split(' ',1);data=json.loads(payload)
        except (ValueError,json.JSONDecodeError):continue
        if isinstance(data,dict) and data.get('event')=='BROKER_REQUEST_STARTED' and data.get('broker_api')=='CASH_ORDER':
            raise LedgerBlocked('WIRE_ATTEMPT_PRESENT')
        if isinstance(data,dict) and 'submission_failure' in data:
            records.append((dt.datetime.fromisoformat(stamp.replace('Z','+00:00')),data))
    if len(records)!=1 or not exact_failure(records[0][1]):raise LedgerBlocked('EXACT_JOB_FAILURE_REQUIRED')
    if not 0<=(aware(records[0][0])-finished).total_seconds()<=10:
        raise LedgerBlocked('JOB_RUN_TIME_BINDING_INVALID')
    reviewer=review.get('reviewer')
    if not isinstance(reviewer,str) or not re.fullmatch('[A-Za-z0-9-]{1,39}',reviewer):raise LedgerBlocked('AUDIT_IDENTITY_REQUIRED')
    try:reviewed=aware(dt.datetime.fromisoformat(review.get('reviewed_at','')))
    except (ValueError,TypeError):raise LedgerBlocked('REVIEW_STALE_OR_INVALID') from None
    if not finished<=reviewed<=now or (now-reviewed).total_seconds()>900:raise LedgerBlocked('REVIEW_STALE_OR_INVALID')
    canonical=json.dumps(review,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    return {'evidence_sha256':hashlib.sha256(canonical).hexdigest(),'reviewer':reviewer,'reviewed_at':reviewed}


def sql_for_pre_send_review(intent,run,review,*,engine_source,workflow_source,job_log,source_evidence,now,env,schema='public'):
    prepared=prepare_pre_send_review(intent,run,review,engine_source=engine_source,
        workflow_source=workflow_source,job_log=job_log,source_evidence=source_evidence,now=now,env=env)
    if schema!='public' and not re.fullmatch('no_order_test_[0-9a-f]{32}',schema):raise LedgerBlocked('SCHEMA_NOT_ALLOWED')
    # Only the known sanitized result shape is copied into SQL, never arbitrary
    # evidence extras, raw logs, source files, or account credentials.
    result_keys={'gate','status','orders_sent','failure_kind','order_outcome','failure_frames',
                 'order_attempts','submission_stage','submission_failure','confirmed_orders_sent'}
    if set(run['result'])!=result_keys or run['result']['order_outcome']!='CHECK_LEDGER':
        raise LedgerBlocked('RUN_RESULT_SHAPE_CHANGED')
    data={'intent_id':intent['intent_id'],'account_fingerprint':intent['account_fingerprint'],
          'trading_day':intent['trading_day'].isoformat(),'ticker':intent['ticker'],
          'created_at':intent['created_at'].isoformat(),'run_id':run['run_id'],
          'started_at':run['started_at'].isoformat(),'finished_at':run['finished_at'].isoformat(),
          'result':run['result'],'reviewer':prepared['reviewer'],'reviewed_at':prepared['reviewed_at'].isoformat()}
    literal=json.dumps(data,sort_keys=True,separators=(',',':'),allow_nan=False).encode().hex()
    key=int(intent['account_fingerprint'][:16],16)%(2**63)
    sql=f"""DO $v519_pre_send_review$
DECLARE
 h jsonb := convert_from(decode('{literal}','hex'),'UTF8')::jsonb;
 i record;
 r record;
 c record;
 n integer;
 at_now timestamptz;
BEGIN
 PERFORM set_config('lock_timeout','5000',true);
 IF NOT pg_try_advisory_xact_lock({key}::bigint) THEN RAISE EXCEPTION 'ACCOUNT_RUNTIME_ALREADY_RUNNING'; END IF;
 SELECT halted INTO c FROM {schema}.v519_control WHERE singleton=true FOR UPDATE;
 IF NOT FOUND OR c.halted IS DISTINCT FROM true THEN RAISE EXCEPTION 'EMERGENCY_HALT_REQUIRED'; END IF;
 SELECT * INTO i FROM {schema}.v519_order_intents
   WHERE intent_id=h->>'intent_id' AND account_fingerprint=h->>'account_fingerprint' FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'INTENT_NOT_FOUND'; END IF;
 SELECT * INTO r FROM {schema}.v519_runs WHERE run_id=h->>'run_id' FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'RUN_NOT_FOUND'; END IF;
 IF EXISTS(SELECT 1 FROM {schema}.v519_pre_send_reviews WHERE intent_id=i.intent_id)
   OR EXISTS(SELECT 1 FROM {schema}.v519_no_order_reviews WHERE intent_id=i.intent_id)
   THEN RAISE EXCEPTION 'PRIOR_RESOLUTION_EXISTS'; END IF;
 at_now := clock_timestamp();
 IF i.status IS DISTINCT FROM 'SUBMISSION_UNKNOWN' OR i.side IS DISTINCT FROM 'BUY'
   OR i.broker_order_id IS NOT NULL OR i.filled_quantity IS DISTINCT FROM 0
   OR i.verified_not_sent IS DISTINCT FROM false OR i.trading_day IS DISTINCT FROM (h->>'trading_day')::date
   OR i.ticker IS DISTINCT FROM h->>'ticker' OR i.created_at IS DISTINCT FROM (h->>'created_at')::timestamptz
   THEN RAISE EXCEPTION 'PRE_SEND_INTENT_STATE_CONFLICT'; END IF;
 IF r.account_fingerprint IS DISTINCT FROM i.account_fingerprint
   OR r.started_at IS DISTINCT FROM (h->>'started_at')::timestamptz
   OR r.finished_at IS DISTINCT FROM (h->>'finished_at')::timestamptz
   OR r.result IS DISTINCT FROM h->'result' OR r.finished_at IS NULL
   OR NOT (r.started_at<=i.created_at AND i.created_at<=r.finished_at AND r.finished_at<=at_now)
   THEN RAISE EXCEPTION 'RUN_BINDING_CHANGED'; END IF;
 SELECT count(*) INTO n FROM {schema}.v519_runs
   WHERE finished_at BETWEEN r.finished_at-interval '10 seconds' AND r.finished_at+interval '10 seconds'
     AND result=r.result;
 IF n<>1 THEN RAISE EXCEPTION 'AMBIGUOUS_RUN_BINDING'; END IF;
 SELECT count(*) INTO n FROM {schema}.v519_order_intents
   WHERE account_fingerprint=i.account_fingerprint AND created_at BETWEEN r.started_at AND r.finished_at;
 IF n<>1 THEN RAISE EXCEPTION 'AMBIGUOUS_INTENT_BINDING'; END IF;
 IF (h->>'reviewed_at')::timestamptz>at_now
   OR at_now-(h->>'reviewed_at')::timestamptz>interval '900 seconds'
   OR (h->>'reviewed_at')::timestamptz<r.finished_at
   THEN RAISE EXCEPTION 'REVIEW_STALE_OR_INVALID'; END IF;
 INSERT INTO {schema}.v519_pre_send_reviews
   (intent_id,account_fingerprint,ledger_run_id,source_commit,engine_blob,workflow_run_id,
    workflow_job_id,log_sha256,evidence_sha256,reviewer,reviewed_at,resolution)
 VALUES (i.intent_id,i.account_fingerprint,r.run_id,'{SOURCE_COMMIT}','{ENGINE_BLOB}',
   {WORKFLOW_RUN},{WORKFLOW_JOB},'{review['log_sha256']}','{prepared['evidence_sha256']}',
   h->>'reviewer',(h->>'reviewed_at')::timestamptz,'OPERATOR_VERIFIED_PRE_SEND_ABORT');
 UPDATE {schema}.v519_order_intents SET status='REJECTED',updated_at=now()
   WHERE intent_id=i.intent_id AND account_fingerprint=i.account_fingerprint;
 GET DIAGNOSTICS n=ROW_COUNT;
 IF n<>1 THEN RAISE EXCEPTION 'PRE_SEND_RESOLUTION_NOT_APPLIED'; END IF;
END;
$v519_pre_send_review$;
"""
    return {'sql':sql,'sql_sha256':hashlib.sha256(sql.encode()).hexdigest(),
            'evidence_sha256':prepared['evidence_sha256']}
