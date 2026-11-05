#!/usr/bin/env python3
"""Validate isolated DDL/constraints with PostgreSQL single-user mode, no sockets.

This cannot validate psycopg transactions, concurrent workers, or API integration.
It never starts a PostgreSQL service or creates a network/Unix socket listener.
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SQL=r'''
INSERT INTO oneil_execution_v2.accounts(account,policy) VALUES (repeat('a',64),'{"version":"test-v1"}');
DO $$ BEGIN IF NOT (SELECT halted FROM oneil_execution_v2.accounts) THEN RAISE EXCEPTION 'default not halted'; END IF; END $$;
INSERT INTO oneil_execution_v2.requests(request_id,account,idempotency_key,payload,digest,approval_id,principal,approval_expires_at,expires_at,control_revision,state,reason)
VALUES ('00000000-0000-0000-0000-000000000001',repeat('a',64),'idempotency_key_1','{"quantity":2,"limit_price":10000,"side":"BUY"}',repeat('b',64),'approval_1','owner',clock_timestamp()+interval '1 minute',clock_timestamp()+interval '1 minute',1,'QUEUED','TEST');
DO $$ BEGIN
 BEGIN UPDATE oneil_execution_v2.requests SET attempts=2; RAISE EXCEPTION 'two attempts allowed'; EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN UPDATE oneil_execution_v2.requests SET state='SUBMISSION_UNKNOWN'; RAISE EXCEPTION 'unknown without attempt allowed'; EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN UPDATE oneil_execution_v2.requests SET filled_quantity=3; RAISE EXCEPTION 'overfill allowed'; EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN UPDATE oneil_execution_v2.accounts SET mode='LIVE'; RAISE EXCEPTION 'live mode allowed'; EXCEPTION WHEN check_violation THEN NULL; END;
END $$;
BEGIN;
SELECT * FROM oneil_execution_v2.requests WHERE account=repeat('a',64) AND (state='QUEUED' OR (state='VALIDATING' AND lease_until < clock_timestamp())) ORDER BY priority DESC,created_at,request_id FOR UPDATE SKIP LOCKED LIMIT 1;
UPDATE oneil_execution_v2.requests SET state='VALIDATING',claim_token='00000000-0000-0000-0000-000000000002',lease_until=clock_timestamp()+interval '30 seconds';
UPDATE oneil_execution_v2.requests SET state='SUBMISSION_UNKNOWN',attempts=1,attempt_id='00000000-0000-0000-0000-000000000003',trading_day=current_date,lease_until=NULL;
COMMIT;
UPDATE oneil_execution_v2.requests SET state='ACKNOWLEDGED',broker_order_id='sim_1';
UPDATE oneil_execution_v2.requests SET state='PART_FILLED',filled_quantity=1;
UPDATE oneil_execution_v2.requests SET state='FILLED',filled_quantity=2;
INSERT INTO oneil_execution_v2.health(account,worker_id,heartbeat_at,reconciled_at,status) VALUES (repeat('a',64),'worker',clock_timestamp(),clock_timestamp(),'IDLE')
ON CONFLICT(account) DO UPDATE SET worker_id=EXCLUDED.worker_id,heartbeat_at=EXCLUDED.heartbeat_at,reconciled_at=COALESCE(EXCLUDED.reconciled_at,oneil_execution_v2.health.reconciled_at),status=EXCLUDED.status;
INSERT INTO oneil_execution_v2.health(account,worker_id,heartbeat_at,reconciled_at,status) VALUES (repeat('a',64),'worker',clock_timestamp(),NULL,'IDLE')
ON CONFLICT(account) DO UPDATE SET worker_id=EXCLUDED.worker_id,heartbeat_at=EXCLUDED.heartbeat_at,reconciled_at=COALESCE(EXCLUDED.reconciled_at,oneil_execution_v2.health.reconciled_at),status=EXCLUDED.status;
DO $$ BEGIN
 BEGIN UPDATE oneil_execution_v2.requests SET payload=jsonb_set(payload,'{quantity}','3'); RAISE EXCEPTION 'content mutable'; EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN UPDATE oneil_execution_v2.requests SET attempts=0; RAISE EXCEPTION 'attempt rewind'; EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN UPDATE oneil_execution_v2.requests SET state='ACKNOWLEDGED'; RAISE EXCEPTION 'state rewind'; EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN UPDATE oneil_execution_v2.requests SET filled_quantity=1; RAISE EXCEPTION 'fill rewind'; EXCEPTION WHEN check_violation THEN NULL; END;
 INSERT INTO oneil_execution_v2.events(account,request_id,event,reason) VALUES (repeat('a',64),'00000000-0000-0000-0000-000000000001','TEST','TEST');
 BEGIN DELETE FROM oneil_execution_v2.events; RAISE EXCEPTION 'audit deleted'; EXCEPTION WHEN check_violation THEN NULL; END;
 IF (SELECT attempts FROM oneil_execution_v2.requests) <> 1 THEN RAISE EXCEPTION 'attempt lost'; END IF;
 IF (SELECT filled_quantity FROM oneil_execution_v2.requests) <> 2 THEN RAISE EXCEPTION 'fill lost'; END IF;
 IF (SELECT state FROM oneil_execution_v2.requests) <> 'FILLED' THEN RAISE EXCEPTION 'state lost'; END IF;
 IF (SELECT reconciled_at FROM oneil_execution_v2.health) IS NULL THEN RAISE EXCEPTION 'reconciliation time lost'; END IF;
 IF (SELECT sum((payload->>'quantity')::numeric*(payload->>'limit_price')::numeric) FROM oneil_execution_v2.requests WHERE attempts=1) <> 20000 THEN RAISE EXCEPTION 'reservation incorrect'; END IF;
END $$;
INSERT INTO oneil_execution_v2.requests(request_id,account,idempotency_key,payload,digest,approval_id,principal,approval_expires_at,expires_at,control_revision,state,reason)
VALUES ('00000000-0000-0000-0000-000000000010',repeat('a',64),'idempotency_policy','{"quantity":1,"limit_price":1000,"side":"BUY","policy_version":"policy-v2-test"}',repeat('c',64),'approval_policy','owner',clock_timestamp()+interval '1 minute',clock_timestamp()+interval '1 minute',1,'QUEUED','TEST');
UPDATE oneil_execution_v2.requests SET state='VALIDATING',claim_token='00000000-0000-0000-0000-000000000011',lease_until=clock_timestamp()+interval '30 seconds' WHERE request_id='00000000-0000-0000-0000-000000000010';
DO $$ BEGIN
 BEGIN UPDATE oneil_execution_v2.requests SET state='SUBMISSION_UNKNOWN',attempts=1,attempt_id='00000000-0000-0000-0000-000000000012' WHERE request_id='00000000-0000-0000-0000-000000000010'; RAISE EXCEPTION 'policy admitted without evidence'; EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN UPDATE oneil_execution_v2.requests SET admission_evidence='{}' WHERE request_id='00000000-0000-0000-0000-000000000010'; RAISE EXCEPTION 'pre-attempt audit accepted'; EXCEPTION WHEN check_violation THEN NULL; END;
END $$;
UPDATE oneil_execution_v2.requests SET state='SUBMISSION_UNKNOWN',attempts=1,attempt_id='00000000-0000-0000-0000-000000000012',admission_evidence=jsonb_build_object('mode','SIMULATION','live_capable',false,'request_id',request_id::text,'order_digest',digest,'control_revision',control_revision) WHERE request_id='00000000-0000-0000-0000-000000000010';
DO $$ BEGIN
 BEGIN UPDATE oneil_execution_v2.requests SET admission_evidence=NULL WHERE request_id='00000000-0000-0000-0000-000000000010'; RAISE EXCEPTION 'policy audit removed'; EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN UPDATE oneil_execution_v2.requests SET admission_evidence=jsonb_set(admission_evidence,'{order_digest}','"changed"') WHERE request_id='00000000-0000-0000-0000-000000000010'; RAISE EXCEPTION 'policy audit changed'; EXCEPTION WHEN check_violation THEN NULL; END;
 IF (SELECT admission_evidence->>'mode' FROM oneil_execution_v2.requests WHERE request_id='00000000-0000-0000-0000-000000000010') <> 'SIMULATION' THEN RAISE EXCEPTION 'policy audit lost'; END IF;
END $$;
'''

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--postgres-bin',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='oneil-v2-offline-') as temp:
        data=Path(temp)/'data'
        subprocess.run([str(args.postgres_bin/'initdb'),'-D',str(data),'--auth-local=trust','--auth-host=reject','--no-locale','--encoding=UTF8'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        # Single-user frontend consumes one complete input line per query. Keep
        # dollar-quoted assertion blocks intact and strip line comments first.
        schema=(ROOT/'oneil_execution_v2'/'schema.sql').read_text(encoding='utf-8')
        source=re.sub(r'--[^\n]*','',schema+'\n'+SQL)
        source=' '.join(source.splitlines())+'\n'
        result=subprocess.run([str(args.postgres_bin/'postgres'),'--single','-D',str(data),'postgres'],input=source,text=True,capture_output=True)
        output=result.stdout+'\n'+result.stderr
        if result.returncode or re.search(r'\b(ERROR|FATAL|PANIC):',output):
            print(output,file=sys.stderr); return 1
        print('PASS: PostgreSQL 16 single-user schema, queue SQL, lifecycle and policy admission audit fences. No sockets. Concurrency and psycopg integration NOT tested.')
        return 0

if __name__=='__main__': raise SystemExit(main())
