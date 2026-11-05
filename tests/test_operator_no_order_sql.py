import copy
import datetime as dt
import hashlib
import json
import re
import os
import unittest
import test_v519_no_order_review as base
from tools.operator_no_order_sql import sql_for_review
from v519_no_order_review import prepare_review
from v519_pg_ledger import LedgerBlocked

ENV=dict(LIVE_ENABLED='false',KIS_ALLOW_ORDER='false')
SOURCE=b'SYNTHETIC user-reported confirmation plus full-history observation; not real evidence'

class GeneratorTests(unittest.TestCase):
    def setUp(self):
        base.ReviewTests.setUp(self)
        self.history['source_evidence_sha256']=hashlib.sha256(SOURCE).hexdigest()
    def build(self,**kwargs):
        return sql_for_review(self.intent,self.history,SOURCE,now=self.now,env=ENV,**kwargs)
    def test_digest_matches_audited_helper_and_source_not_embedded(self):
        result=self.build()
        self.assertEqual(result['evidence_sha256'],prepare_review(self.intent,self.history,now=self.now)['evidence_sha256'])
        self.assertEqual(result['sql_sha256'],hashlib.sha256(result['sql'].encode()).hexdigest())
        self.assertNotIn(SOURCE.decode(),result['sql'])
        self.assertIn('pg_try_advisory_xact_lock',result['sql'])
        self.assertNotIn('UPDATE public.v519_control',result['sql'])
        self.assertNotIn('v519_live_activation',result['sql'])
    def test_delimiter_injection_is_hex_encoded(self):
        self.history['extra']="'$v519_operator_review$; DELETE FROM secret; --"
        sql=self.build()['sql']
        self.assertNotIn('DELETE FROM secret',sql)
        self.assertEqual(sql.count('$v519_operator_review$'),2)
        decoded=json.loads(bytes.fromhex(re.search(r"decode\('([0-9a-f]+)'",sql).group(1)))
        self.assertEqual(set(decoded),{'intent_id','account_fingerprint','reviewer','reviewed_at'})
        self.assertNotIn('extra',decoded)
        self.assertNotIn('rows',decoded)
    def test_false_attestation_bad_source_flags_and_schema_blocked(self):
        self.history['operator_approved']=False
        with self.assertRaises(LedgerBlocked):self.build()
        self.history['operator_approved']=True
        with self.assertRaises(LedgerBlocked):
            sql_for_review(self.intent,self.history,b'wrong',now=self.now,env=ENV)
        with self.assertRaises(LedgerBlocked):
            sql_for_review(self.intent,self.history,SOURCE,now=self.now,env={})
        with self.assertRaises(LedgerBlocked):self.build(schema='public;DROP TABLE x')

@unittest.skipUnless(os.getenv('V519_TEST_DB_URL'),'Local PostgreSQL test URL not supplied')
class SqlPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):base.PostgresReviewTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):base.PostgresReviewTests.tearDownClass.__func__(cls)
    def setUp(self):
        base.PostgresReviewTests.setUp(self)
        self.now=self.conn.execute('SELECT clock_timestamp()').fetchone()[0]
        self.history['reviewed_at']=self.now.isoformat()
        self.history['source_evidence_sha256']=hashlib.sha256(SOURCE).hexdigest()
    def build(self):
        return sql_for_review(self.intent,self.history,SOURCE,now=self.now,env=ENV,schema=self.schema)
    def resolve(self):self.conn.execute(self.build()['sql'])
    def unchanged(self):base.PostgresReviewTests.assert_unresolved_and_unaudited(self)
    def test_commit_matches_helper_audit_and_preserves_every_other_field(self):
        before=self.ledger.rows('SELECT * FROM v519_order_intents')[0]
        control=self.ledger.rows('SELECT * FROM v519_control')
        activation=self.ledger.rows('SELECT * FROM v519_live_activation')
        built=self.build();self.conn.execute(built['sql'])
        after=self.ledger.rows('SELECT * FROM v519_order_intents')[0]
        self.assertEqual(after['status'],'REJECTED')
        for k in before.keys()-{'status','updated_at'}:self.assertEqual(before[k],after[k])
        self.assertEqual(control,self.ledger.rows('SELECT * FROM v519_control'))
        self.assertEqual(activation,self.ledger.rows('SELECT * FROM v519_live_activation'))
        audit=self.ledger.rows('SELECT * FROM v519_no_order_reviews')
        self.assertEqual(len(audit),1);self.assertEqual(audit[0]['evidence_sha256'],built['evidence_sha256'])
        self.assertEqual(self.ledger.bought_today(self.intent['trading_day']),{'000500'})
    def test_repeated_resolution_fails(self):
        self.resolve()
        with self.assertRaises(Exception):self.resolve()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM v519_no_order_reviews').fetchone()[0],1)
    def test_zero_update_rolls_back_audit(self):
        self.conn.execute('CREATE FUNCTION skip_review_update() RETURNS trigger AS $$ BEGIN RETURN NULL; END; $$ LANGUAGE plpgsql')
        self.conn.execute('CREATE TRIGGER skip_review_update BEFORE UPDATE ON v519_order_intents FOR EACH ROW EXECUTE FUNCTION skip_review_update()')
        try:
            with self.assertRaises(Exception):self.resolve()
            self.unchanged()
        finally:
            self.conn.execute('DROP TRIGGER skip_review_update ON v519_order_intents');self.conn.execute('DROP FUNCTION skip_review_update()')
    def test_audit_failure_rolls_back_all(self):
        self.conn.execute("ALTER TABLE v519_no_order_reviews ADD CONSTRAINT reject_test CHECK(reviewer <> 'operator')")
        try:
            with self.assertRaises(Exception):self.resolve()
            self.unchanged()
        finally:self.conn.execute('ALTER TABLE v519_no_order_reviews DROP CONSTRAINT reject_test')
    def test_runtime_session_lock_blocks_xact_lock(self):
        import psycopg
        with psycopg.connect(self.url,autocommit=True) as other:
            other.execute('SELECT pg_advisory_lock(%s)',(self.ledger.lock_key,))
            try:
                with self.assertRaises(Exception):self.resolve()
                self.unchanged()
            finally:other.execute('SELECT pg_advisory_unlock(%s)',(self.ledger.lock_key,))
    def test_halt_and_changed_state_rechecked_at_execution(self):
        sql=self.build()['sql']
        for statement in ("UPDATE v519_control SET halted=false", "UPDATE v519_order_intents SET broker_order_id='found-order'"):
            self.conn.execute(statement)
            with self.assertRaises(Exception):self.conn.execute(sql)
            self.assertEqual(self.conn.execute('SELECT count(*) FROM v519_no_order_reviews').fetchone()[0],0)
            self.conn.execute('UPDATE v519_control SET halted=true');self.conn.execute('UPDATE v519_order_intents SET broker_order_id=NULL')
        self.unchanged()
    def test_server_clock_rejects_stale_generated_sql(self):
        # Generate against a past trusted preview clock, then execute now.
        self.now-=dt.timedelta(minutes=16);self.history['reviewed_at']=self.now.isoformat()
        with self.assertRaises(Exception):self.resolve()
        self.unchanged()

if __name__=='__main__':unittest.main()
