import copy
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
import unittest
import test_v519_no_order_review as base
from v519_pg_ledger import LedgerBlocked,PgRuntimeLedger
from v519_pre_send_review import *

ENV={'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}
FIXTURES=Path(__file__).parent/'fixtures'
ENGINE=(FIXTURES/'pre_send_engine_abf446b.txt').read_bytes()
WORKFLOW=(FIXTURES/'pre_send_workflow_abf446b.txt').read_bytes()
SOURCE=b'SYNTHETIC OPERATOR PROVENANCE PACKET, not real account evidence'

class PreSendTests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime.now(dt.timezone.utc)
        self.day=(self.now-dt.timedelta(seconds=60)).astimezone(KST).date()
        self.fp='b'*64
        self.ident=hashlib.sha256(f'v519|{self.fp}|{self.day}|000500|BUY'.encode()).hexdigest()
        self.intent={'intent_id':self.ident,'account_fingerprint':self.fp,'trading_day':self.day,'ticker':'000500',
          'side':'BUY','status':'SUBMISSION_UNKNOWN','broker_order_id':None,'filled_quantity':0,'verified_not_sent':False,
          'created_at':self.now-dt.timedelta(seconds=40)}
        self.result={'gate':'DENY','status':'BLOCKED','orders_sent':None,'failure_kind':'INVALID_VALUE',
          'order_outcome':'CHECK_LEDGER','failure_frames':copy.deepcopy(TOP_FRAMES),'order_attempts':1,
          'submission_stage':'SUBMIT','submission_failure':{'failure_kind':'INVALID_VALUE','failure_code':CODE,'failure_frames':copy.deepcopy(FRAMES)},
          'confirmed_orders_sent':0}
        self.run={'run_id':'c'*32,'account_fingerprint':self.fp,'started_at':self.now-dt.timedelta(seconds=60),
                  'finished_at':self.now-dt.timedelta(seconds=30),'result':self.result}
        self.log=(f'checkout {SOURCE_COMMIT}\n'+(self.run['finished_at']+dt.timedelta(seconds=1)).isoformat()+' '+json.dumps(self.result)+'\n').encode()
        self.review={'intent_id':self.ident,'account_fingerprint':self.fp,'ledger_run_id':self.run['run_id'],
          'source_commit':SOURCE_COMMIT,'workflow_run_id':WORKFLOW_RUN,'workflow_job_id':WORKFLOW_JOB,
          'github_origin_verified':True,'checkout_unchanged_verified':True,'unique_run_binding_verified':True,
          'operator_approved':True,'matching_run_count':1,'matching_intent_count':1,'reviewer':'synthetic-ai-reviewer',
          'reviewed_at':self.now.isoformat(),'source_evidence_sha256':hashlib.sha256(SOURCE).hexdigest(),
          'log_sha256':hashlib.sha256(self.log).hexdigest()}
    def build(self,**kwargs):
        params=dict(engine_source=ENGINE,workflow_source=WORKFLOW,job_log=self.log,source_evidence=SOURCE,now=self.now,env=ENV)
        params.update(kwargs)
        return sql_for_pre_send_review(self.intent,self.run,self.review,**params)
    def test_same_day_proof_valid_and_source_pins_match(self):
        self.assertEqual(blob_sha(ENGINE),ENGINE_BLOB);self.assertEqual(blob_sha(WORKFLOW),WORKFLOW_BLOB)
        out=self.build();self.assertEqual(len(out['evidence_sha256']),64)
        self.assertNotIn('SET halted',out['sql']);self.assertNotIn('v519_live_activation',out['sql'])
        self.assertNotIn('verified_not_sent=true',out['sql'])
    def test_sql_payload_excludes_review_extras_and_raw_evidence(self):
        self.review['private_extra']='PRIVATE-SOURCE-NOT-FOR-SQL'
        sql=self.build()['sql']
        decoded=json.loads(bytes.fromhex(re.search(r"decode\('([0-9a-f]+)'",sql).group(1)))
        self.assertEqual(set(decoded),{'intent_id','account_fingerprint','trading_day','ticker','created_at',
          'run_id','started_at','finished_at','result','reviewer','reviewed_at'})
        self.assertNotIn('PRIVATE-SOURCE-NOT-FOR-SQL',sql)
        self.assertNotIn(SOURCE.decode(),sql)
    def test_changed_source_workflow_or_origin_denied(self):
        for extra in ({'engine_source':ENGINE+b'\n'},{'workflow_source':WORKFLOW+b'\n'},{'env':{}},{'source_evidence':b'other'}):
            with self.assertRaises(LedgerBlocked):self.build(**extra)
        for k in ('github_origin_verified','checkout_unchanged_verified','unique_run_binding_verified','operator_approved'):
            saved=self.review[k];self.review[k]=False
            with self.assertRaises(LedgerBlocked):self.build()
            self.review[k]=saved
    def test_error_message_alone_or_other_stage_not_proof(self):
        for k,v in (('submission_stage','ACK_VALIDATE'),('confirmed_orders_sent',1),('order_attempts',2)):
            saved=self.result[k];self.result[k]=v
            with self.assertRaises(LedgerBlocked):self.build()
            self.result[k]=saved
        self.result['submission_failure']['failure_frames'][-1]['line']=566
        with self.assertRaises(LedgerBlocked):self.build()
    def test_wire_attempt_and_duplicate_terminal_log_denied(self):
        for extra in (self.log, (self.now.isoformat()+' '+json.dumps({'event':'BROKER_REQUEST_STARTED','broker_api':'CASH_ORDER'})+'\n').encode()):
            log=self.log+extra;self.review['log_sha256']=hashlib.sha256(log).hexdigest()
            with self.assertRaises(LedgerBlocked):self.build(job_log=log)
    def test_ambiguous_counts_account_and_deterministic_id_denied(self):
        self.review['matching_run_count']=2
        with self.assertRaises(LedgerBlocked):self.build()
        self.review['matching_run_count']=1;self.run['account_fingerprint']='d'*64
        with self.assertRaises(LedgerBlocked):self.build()
        self.run['account_fingerprint']=self.fp;self.intent['intent_id']='a'*64
        with self.assertRaises(LedgerBlocked):self.build()
    def test_filled_acknowledged_or_nonbuy_denied(self):
        for k,v in (('filled_quantity',1),('broker_order_id','1'),('status','ACKNOWLEDGED'),('side','SELL'),('verified_not_sent',True)):
            old=self.intent[k];self.intent[k]=v
            with self.assertRaises(LedgerBlocked):self.build()
            self.intent[k]=old
    def test_expired_review_or_unfinished_run_denied(self):
        self.review['reviewed_at']=(self.now-dt.timedelta(minutes=16)).isoformat()
        with self.assertRaises(LedgerBlocked):self.build()
        self.review['reviewed_at']=self.now.isoformat();self.run['finished_at']=None
        with self.assertRaises(LedgerBlocked):self.build()

@unittest.skipUnless(os.getenv('V519_TEST_DB_URL'),'Local PostgreSQL test URL not supplied')
class PreSendPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base.PostgresReviewTests.setUpClass.__func__(cls)
        cls.conn.execute(AUDIT_SCHEMA)
    @classmethod
    def tearDownClass(cls):base.PostgresReviewTests.tearDownClass.__func__(cls)
    def setUp(self):
        PreSendTests.setUp(self)
        self.conn.execute('TRUNCATE v519_pre_send_reviews,v519_no_order_reviews,v519_order_intents,v519_runs')
        self.conn.execute('UPDATE v519_control SET halted=true')
        from psycopg.types.json import Jsonb
        self.conn.execute('INSERT INTO v519_runs(run_id,account_fingerprint,started_at,finished_at,result) VALUES(%s,%s,%s,%s,%s)',
          (self.run['run_id'],self.fp,self.run['started_at'],self.run['finished_at'],Jsonb(self.result)))
        self.conn.execute("INSERT INTO v519_order_intents(intent_id,account_fingerprint,trading_day,ticker,side,venue,quantity,created_at) VALUES(%s,%s,%s,'000500','BUY','KRX',1,%s)",
          (self.ident,self.fp,self.day,self.intent['created_at']))
        self.ledger=PgRuntimeLedger(self.conn,self.fp)
    def sql(self):return PreSendTests.build(self,schema=self.schema)['sql']
    def unchanged(self):
        self.assertEqual(self.ledger.pending()[0]['status'],'SUBMISSION_UNKNOWN')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM v519_pre_send_reviews').fetchone()[0],0)
    def test_same_day_commit_preserves_halt_activation_identity_and_daily_unique(self):
        import psycopg
        before=self.ledger.rows('SELECT * FROM v519_order_intents')[0];control=self.ledger.rows('SELECT * FROM v519_control')
        activation=self.ledger.rows('SELECT * FROM v519_live_activation')
        self.conn.execute(self.sql());after=self.ledger.rows('SELECT * FROM v519_order_intents')[0]
        self.assertEqual(after['status'],'REJECTED')
        for k in before.keys()-{'status','updated_at'}:self.assertEqual(before[k],after[k])
        self.assertEqual(control,self.ledger.rows('SELECT * FROM v519_control'));self.assertEqual(activation,self.ledger.rows('SELECT * FROM v519_live_activation'))
        self.assertEqual(self.ledger.bought_today(self.day),{'000500'})
        self.assertEqual(self.conn.execute('SELECT resolution FROM v519_pre_send_reviews').fetchone()[0],'OPERATOR_VERIFIED_PRE_SEND_ABORT')
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.conn.execute("INSERT INTO v519_order_intents(intent_id,account_fingerprint,trading_day,ticker,side,venue,quantity) VALUES(%s,%s,%s,'000500','BUY','KRX',1)",('e'*64,self.fp,self.day))
    def test_runtime_session_lock_prevents_resolution(self):
        import psycopg
        with psycopg.connect(self.url,autocommit=True) as other:
            other.execute('SELECT pg_advisory_lock(%s)',(self.ledger.lock_key,))
            try:
                with self.assertRaisesRegex(Exception,'ACCOUNT_RUNTIME_ALREADY_RUNNING'):self.conn.execute(self.sql())
                self.unchanged()
            finally:other.execute('SELECT pg_advisory_unlock(%s)',(self.ledger.lock_key,))
    def test_audit_failure_and_zero_row_update_roll_back(self):
        self.conn.execute("ALTER TABLE v519_pre_send_reviews ADD CONSTRAINT deny_test CHECK(reviewer<>'synthetic-ai-reviewer')")
        try:
            with self.assertRaises(Exception):self.conn.execute(self.sql())
            self.unchanged()
        finally:self.conn.execute('ALTER TABLE v519_pre_send_reviews DROP CONSTRAINT deny_test')
        self.conn.execute('CREATE FUNCTION skip_pre_send() RETURNS trigger AS $$ BEGIN RETURN NULL; END; $$ LANGUAGE plpgsql')
        self.conn.execute('CREATE TRIGGER skip_pre_send BEFORE UPDATE ON v519_order_intents FOR EACH ROW EXECUTE FUNCTION skip_pre_send()')
        try:
            with self.assertRaisesRegex(Exception,'PRE_SEND_RESOLUTION_NOT_APPLIED'):self.conn.execute(self.sql())
            self.unchanged()
        finally:self.conn.execute('DROP TRIGGER skip_pre_send ON v519_order_intents');self.conn.execute('DROP FUNCTION skip_pre_send()')
    def test_changed_run_or_competing_run_denied(self):
        sql=self.sql();self.conn.execute("UPDATE v519_runs SET result=result || '{\"submission_stage\":\"ACK_VALIDATE\"}'::jsonb")
        with self.assertRaisesRegex(Exception,'RUN_BINDING_CHANGED'):self.conn.execute(sql)
        self.unchanged()
    def test_ambiguous_run_and_reservation_denied(self):
        sql=self.sql()
        self.conn.execute("INSERT INTO v519_runs SELECT %s,%s,started_at,finished_at,result FROM v519_runs",('d'*32,'e'*64))
        with self.assertRaisesRegex(Exception,'AMBIGUOUS_RUN_BINDING'):self.conn.execute(sql)
        self.unchanged();self.conn.execute('DELETE FROM v519_runs WHERE run_id=%s',('d'*32,))
        self.conn.execute("INSERT INTO v519_order_intents(intent_id,account_fingerprint,trading_day,ticker,side,venue,quantity,created_at) VALUES(%s,%s,%s,'005930','BUY','KRX',1,%s)",('f'*64,self.fp,self.day,self.intent['created_at']))
        with self.assertRaisesRegex(Exception,'AMBIGUOUS_INTENT_BINDING'):self.conn.execute(sql)
        self.unchanged()
    def test_prior_other_resolution_blocks_even_if_state_was_reset(self):
        self.conn.execute("INSERT INTO v519_no_order_reviews(intent_id,account_fingerprint,evidence_sha256,reviewer,reviewed_at,resolution) VALUES(%s,%s,%s,'prior-review',%s,'OPERATOR_VERIFIED_NO_ORDER')",
          (self.ident,self.fp,'a'*64,self.now))
        with self.assertRaisesRegex(Exception,'PRIOR_RESOLUTION_EXISTS'):self.conn.execute(self.sql())
        self.unchanged()
    def test_sql_server_clock_blocks_previously_valid_expired_review(self):
        delta=dt.timedelta(minutes=16)
        self.now-=delta;self.intent['created_at']-=delta
        self.run['started_at']-=delta;self.run['finished_at']-=delta
        self.review['reviewed_at']=self.now.isoformat()
        self.day=self.run['started_at'].astimezone(KST).date()
        self.ident=hashlib.sha256(f'v519|{self.fp}|{self.day}|000500|BUY'.encode()).hexdigest()
        self.intent['intent_id']=self.ident;self.intent['trading_day']=self.day;self.review['intent_id']=self.ident
        self.conn.execute('UPDATE v519_order_intents SET intent_id=%s,trading_day=%s',(self.ident,self.day))
        self.log=(f'checkout {SOURCE_COMMIT}\n'+(self.run['finished_at']+dt.timedelta(seconds=1)).isoformat()+' '+json.dumps(self.result)+'\n').encode()
        self.review['log_sha256']=hashlib.sha256(self.log).hexdigest()
        self.conn.execute('UPDATE v519_order_intents SET created_at=%s',(self.intent['created_at'],))
        self.conn.execute('UPDATE v519_runs SET started_at=%s,finished_at=%s',(self.run['started_at'],self.run['finished_at']))
        with self.assertRaisesRegex(Exception,'REVIEW_STALE_OR_INVALID'):self.conn.execute(self.sql())
        self.unchanged()
    def test_halt_required_and_repeat_fails_closed(self):
        sql=self.sql();self.conn.execute('UPDATE v519_control SET halted=false')
        with self.assertRaisesRegex(Exception,'EMERGENCY_HALT_REQUIRED'):self.conn.execute(sql)
        self.unchanged();self.conn.execute('UPDATE v519_control SET halted=true');self.conn.execute(sql)
        with self.assertRaisesRegex(Exception,'PRIOR_RESOLUTION_EXISTS'):self.conn.execute(sql)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM v519_pre_send_reviews').fetchone()[0],1)

if __name__=='__main__':unittest.main()
