import os
import threading
import time
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from oneil_execution_v2.api import AuthenticatedOwner, SimulationAPI
from oneil_execution_v2.broker import SimulatedBroker
from oneil_execution_v2.domain import Approval, Blocked, Order, RiskPolicy
from oneil_execution_v2.store import PostgresStore, S
from oneil_execution_v2.worker import Worker

@unittest.skipUnless(os.environ.get('ONEIL_V2_TEST_DSN'), 'Explicit isolated PostgreSQL test DSN required')
class PostgresTests(unittest.TestCase):
    def setUp(self):
        self.store=PostgresStore(os.environ['ONEIL_V2_TEST_DSN'])
        self.store.initialize()
        self.account=uuid.uuid4().hex*2
        self.policy=RiskPolicy('test-v1',100000,1000000,500000,2000000,100000)
        self.store.register_simulation_account(self.account,self.policy)
        self.api=SimulationAPI(self.store)
        self.owner=AuthenticatedOwner('test-owner',self.account)
        self.api.resume_simulation(self.owner,1)
        self.broker=SimulatedBroker(lambda: datetime.now(timezone.utc))
        self.worker=Worker(self.store,self.broker,core_fixture_only=True)

    def tearDown(self): self.store.close()

    def enqueue(self, **changes):
        order=Order(self.account,'005930','BUY','KRX',2,10000,self.store.now()+timedelta(minutes=1),'test-v1')
        order=replace(order,**changes)
        result=self.api.confirm_order(self.owner,order,idempotency_key=uuid.uuid4().hex)
        return self.store.get(self.account,result['request_id'])

    def fresh_store(self): return PostgresStore(os.environ['ONEIL_V2_TEST_DSN'])
    def get(self,row): return self.store.get(self.account,row['request_id'])

    def test_default_halted(self):
        other='b'*64
        try: self.store.register_simulation_account(other,self.policy)
        except Blocked: pass
        self.assertTrue(self.store.control(other)['halted'])

    def test_idempotency_same_receipt_and_conflict(self):
        order=Order(self.account,'005930','BUY','KRX',2,10000,self.store.now()+timedelta(minutes=1),'test-v1')
        key=uuid.uuid4().hex
        one=self.api.confirm_order(self.owner,order,idempotency_key=key)
        two=self.api.confirm_order(self.owner,order,idempotency_key=key)
        self.assertEqual(one['request_id'],two['request_id'])
        with self.assertRaisesRegex(Blocked,'IDEMPOTENCY_CONFLICT'):
            self.api.confirm_order(self.owner,replace(order,quantity=3),idempotency_key=key)

    def test_approval_cannot_be_reused(self):
        order=Order(self.account,'005930','BUY','KRX',2,10000,self.store.now()+timedelta(minutes=1),'test-v1')
        approval=Approval(uuid.uuid4().hex,'owner',order.digest,order.expires_at)
        self.store.enqueue(order,approval,uuid.uuid4().hex)
        with self.assertRaisesRegex(Blocked,'APPROVAL_ALREADY_USED'): self.store.enqueue(order,approval,uuid.uuid4().hex)

    def test_ack_is_not_fill(self):
        row=self.enqueue()
        self.assertEqual(self.worker.tick(self.account),'ACKNOWLEDGED')
        self.assertEqual(self.get(row)['filled_quantity'],0)
        self.assertEqual(len(self.broker.calls),1)
        self.worker.tick(self.account)
        self.assertEqual(len(self.broker.calls),1)

    def test_partial_then_fill(self):
        row=self.enqueue(); self.broker.behavior='partial'
        self.worker.tick(self.account)
        self.assertEqual(self.get(row)['state'],'PART_FILLED')
        self.broker.advance(str(row['request_id']),status='FILLED',filled_quantity=2)
        self.worker.tick(self.account)
        self.assertEqual(self.get(row)['state'],'FILLED')
        self.assertEqual(len(self.broker.calls),1)

    def test_fill_regression_halts(self):
        row=self.enqueue(); self.broker.behavior='partial'; self.worker.tick(self.account)
        key=str(row['request_id'])
        self.broker.orders[key]=replace(self.broker.orders[key],status='ACKNOWLEDGED',filled_quantity=0)
        self.assertEqual(self.worker.tick(self.account),'RECONCILIATION_BLOCKED')
        self.assertTrue(self.store.control(self.account)['halted'])
        self.assertEqual(self.get(row)['filled_quantity'],1)

    def test_lost_ack_reconciles_without_resend(self):
        row=self.enqueue(); self.broker.behavior='timeout_after_accept'
        self.assertEqual(self.worker.tick(self.account),'SUBMISSION_UNKNOWN')
        self.assertTrue(self.store.control(self.account)['halted'])
        Worker(self.store,self.broker,core_fixture_only=True).tick(self.account)
        self.assertEqual(self.get(row)['state'],'ACKNOWLEDGED')
        self.assertEqual(len(self.broker.calls),1)
        self.assertTrue(self.store.control(self.account)['halted'])

    def test_absent_history_never_retries_unknown(self):
        row=self.enqueue(); self.broker.behavior='timeout_before_accept'
        self.worker.tick(self.account)
        for _ in range(3): Worker(self.store,self.broker,core_fixture_only=True).tick(self.account)
        self.assertEqual(self.get(row)['state'],'SUBMISSION_UNKNOWN')
        self.assertEqual(len(self.broker.calls),1)
        with self.assertRaisesRegex(Blocked,'UNRESOLVED_ORDER'):
            self.api.resume_simulation(self.owner,self.store.control(self.account)['revision'])

    def test_crash_after_send_before_ack_commit(self):
        row=self.enqueue(); self.broker.behavior='crash_after_accept'
        with self.assertRaises(SystemExit): self.worker.tick(self.account)
        self.assertEqual(self.get(row)['state'],'SUBMISSION_UNKNOWN')
        Worker(self.store,self.broker,core_fixture_only=True).tick(self.account)
        self.assertEqual(self.get(row)['state'],'ACKNOWLEDGED')
        self.assertEqual(len(self.broker.calls),1)

    def test_crash_after_reservation_before_send(self):
        row=self.enqueue(); claimed=self.store.claim(self.account)
        self.store.reserve_attempt(claimed,self.broker.session(Order.from_payload(row['payload'])).trading_day,self.policy,lambda now,daily:None)
        self.worker.tick(self.account)
        self.assertEqual(self.get(row)['state'],'SUBMISSION_UNKNOWN')
        self.assertTrue(self.store.control(self.account)['halted'])
        self.assertEqual(self.broker.calls,[])

    def test_validation_lease_recovered_fenced(self):
        row=self.enqueue(); first=self.store.claim(self.account)
        self.store.conn.execute(f"UPDATE {S}requests SET lease_until=clock_timestamp()-interval '1 second' WHERE request_id=%s",(row['request_id'],))
        second=self.store.claim(self.account)
        with self.assertRaisesRegex(Blocked,'CLAIM_LOST'):
            self.store.reserve_attempt(first,'2026-11-05',self.policy,lambda now,daily:None)
        self.assertNotEqual(first['claim_token'],second['claim_token'])
        self.assertFalse(self.store.finish_before_send(first,'BLOCKED','STALE_WORKER'))

    def test_two_connections_cannot_claim_same_request(self):
        row=self.enqueue(); other=self.fresh_store()
        try:
            first=self.store.claim(self.account)
            self.assertIsNone(other.claim(self.account))
            self.assertEqual(first['request_id'],row['request_id'])
        finally: other.close()

    def test_account_session_lock_excludes_other_worker(self):
        other=self.fresh_store()
        try:
            with self.store.account_lock(self.account) as locked:
                self.assertTrue(locked)
                with other.account_lock(self.account) as acquired: self.assertFalse(acquired)
            with other.account_lock(self.account) as acquired: self.assertTrue(acquired)
        finally: other.close()

    def test_two_workers_one_broker_effect(self):
        row=self.enqueue(); start=threading.Barrier(2); results=[]
        def run():
            store=self.fresh_store()
            try:
                start.wait(); results.append(Worker(store,self.broker,core_fixture_only=True).tick(self.account))
            finally: store.close()
        threads=[threading.Thread(target=run) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10); self.assertFalse(thread.is_alive())
        self.assertEqual(len(results),2)
        self.assertEqual(len(self.broker.calls),1)
        self.assertEqual(self.get(row)['attempts'],1)

    def test_halt_between_claim_and_reservation(self):
        row=self.enqueue(); claimed=self.store.claim(self.account)
        self.api.halt(self.owner,self.store.control(self.account)['revision'])
        with self.assertRaisesRegex(Blocked,'HALTED'):
            self.store.reserve_attempt(claimed,'2026-11-05',self.policy,lambda now,daily:None)
        self.assertEqual(self.get(row)['attempts'],0)

    def test_halt_after_admission_does_not_fake_cancel(self):
        row=self.enqueue(); other=self.fresh_store()
        try:
            self.broker.submit_hook=lambda: other.set_halt(self.account,halted=True,expected_revision=other.control(self.account)['revision'])
            self.worker.tick(self.account)
            self.assertEqual(self.get(row)['state'],'ACKNOWLEDGED')
            self.assertTrue(self.store.control(self.account)['halted'])
        finally: other.close()

    def test_control_change_invalidates_pending_authorization(self):
        row=self.enqueue(); rev=self.store.control(self.account)['revision']
        rev=self.api.halt(self.owner,rev); self.api.resume_simulation(self.owner,rev)
        self.worker.tick(self.account)
        self.assertEqual(self.get(row)['reason'],'CONTROL_CHANGED')
        self.assertEqual(self.broker.calls,[])

    def test_expired_request_does_not_discard_other_request(self):
        first=self.enqueue(expires_at=self.store.now()+timedelta(milliseconds=100)); second=self.enqueue(ticker='000660')
        time.sleep(0.15)
        self.assertEqual(self.worker.tick(self.account),'EXPIRED')
        self.assertEqual(self.worker.tick(self.account),'ACKNOWLEDGED')
        self.assertEqual(self.get(second)['state'],'ACKNOWLEDGED')

    def test_one_bad_request_does_not_cancel_queue(self):
        first=self.enqueue(limit_price=12000); second=self.enqueue(ticker='000660')
        self.assertEqual(self.worker.tick(self.account),'BLOCKED')
        self.assertEqual(self.get(first)['reason'],'PRICE_DEVIATION')
        self.assertEqual(self.worker.tick(self.account),'ACKNOWLEDGED')
        self.assertEqual(self.get(second)['state'],'ACKNOWLEDGED')

    def test_cancel_queued_and_claimed(self):
        for claim in (False,True):
            row=self.enqueue()
            if claim: self.store.claim(self.account)
            self.api.cancel_pending(self.owner,row['request_id'])
            self.worker.tick(self.account)
            self.assertEqual(self.get(row)['state'],'CANCELLED')
        self.assertEqual(self.broker.calls,[])

    def test_cancel_after_admission_requires_broker_cancel(self):
        row=self.enqueue(); self.worker.tick(self.account)
        with self.assertRaisesRegex(Blocked,'BROKER_CANCEL_REQUIRED'): self.api.cancel_pending(self.owner,row['request_id'])

    def test_unresolved_blocks_second_request(self):
        first=self.enqueue(); second=self.enqueue()
        self.worker.tick(self.account); self.worker.tick(self.account)
        self.assertEqual(self.get(second)['state'],'QUEUED')
        self.assertEqual(len(self.broker.calls),1)

    def test_stale_quote_no_broker_effect(self):
        row=self.enqueue(); self.broker.quote_offset=timedelta(seconds=-30)
        self.worker.tick(self.account)
        self.assertEqual(self.get(row)['reason'],'QUOTE_STALE')
        self.assertEqual(self.broker.calls,[])

    def test_incomplete_history_halts_before_claim(self):
        row=self.enqueue(); self.broker.history_complete=False
        self.assertEqual(self.worker.tick(self.account),'RECONCILIATION_BLOCKED')
        self.assertEqual(self.get(row)['state'],'QUEUED')
        self.assertEqual(self.broker.calls,[])

    def test_stale_history_halts(self):
        self.enqueue(); self.broker.history_offset=timedelta(seconds=-30)
        self.assertEqual(self.worker.tick(self.account),'RECONCILIATION_BLOCKED')
        self.assertEqual(self.broker.calls,[])

    def test_daily_loss_blocks(self):
        row=self.enqueue(); self.broker.daily_loss_krw=100000
        self.worker.tick(self.account)
        self.assertEqual(self.get(row)['reason'],'DAILY_LOSS_LIMIT')
        self.assertEqual(self.broker.calls,[])

    def test_daily_notional_reservation(self):
        row=self.enqueue(); self.broker.behavior='fill'; self.worker.tick(self.account)
        self.assertEqual(self.store.daily_buy(self.account,str(self.get(row)['trading_day'])),20000)

    def test_db_one_attempt_constraint(self):
        row=self.enqueue(); self.worker.tick(self.account)
        import psycopg
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.store.conn.execute(f'UPDATE {S}requests SET attempts=2 WHERE request_id=%s',(row['request_id'],))

    def test_read_model_separates_health_and_order_state(self):
        self.enqueue(); self.worker.tick(self.account)
        status=self.api.status(self.owner)
        self.assertFalse(status['live_capable'])
        self.assertEqual(status['mode'],'SIMULATION')
        self.assertIsNotNone(status['health']['reconciled_at'])
        self.assertEqual(status['requests'][0]['state'],'ACKNOWLEDGED')

    def test_auth_account_boundary(self):
        row=self.enqueue()
        with self.assertRaises(Blocked): self.api.status(AuthenticatedOwner('owner','c'*64))
        with self.assertRaisesRegex(Blocked,'AUTHENTICATED_OWNER_REQUIRED'): self.api.status({'account':self.account})

    def test_live_adapter_rejected(self):
        with self.assertRaisesRegex(Blocked,'LIVE_BROKER_NOT_IMPLEMENTED'): Worker(self.store,object(),core_fixture_only=True)

    def test_audit_captures_attempt_before_ack(self):
        row=self.enqueue(); self.worker.tick(self.account)
        events=self.store.conn.execute(f'SELECT event FROM {S}events WHERE request_id=%s ORDER BY sequence',(row['request_id'],)).fetchall()
        self.assertEqual([x['event'] for x in events],['REQUEST_ACCEPTED','CLAIMED','ATTEMPT_ADMITTED','BROKER_OBSERVATION'])


    def test_same_connection_lock_not_reentrant(self):
        with self.store.account_lock(self.account) as first:
            self.assertTrue(first)
            with self.store.account_lock(self.account) as second: self.assertFalse(second)

    def test_connection_cannot_cross_threads(self):
        results=[]
        def access():
            try: self.store.control(self.account)
            except Blocked as error: results.append(str(error))
        thread=threading.Thread(target=access); thread.start(); thread.join(5)
        self.assertEqual(results,['DEDICATED_CONNECTION_REQUIRED'])

    def test_sql_content_and_audit_immutable(self):
        import psycopg
        row=self.enqueue()
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.store.conn.execute(f"UPDATE {S}requests SET payload=jsonb_set(payload,'{{quantity}}','3') WHERE request_id=%s",(row['request_id'],))
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.store.conn.execute(f'DELETE FROM {S}events WHERE request_id=%s',(row['request_id'],))

    def test_stale_causal_account_evidence_blocks(self):
        first=self.enqueue(); self.broker.behavior='fill'; self.worker.tick(self.account)
        self.broker.balances[self.account]['fills']={}
        second=self.enqueue()
        self.assertEqual(self.worker.tick(self.account),'BLOCKED')
        self.assertEqual(self.get(second)['reason'],'ACCOUNT_NOT_CAUSALLY_RECONCILED')
        self.assertEqual(len(self.broker.calls),1)

    def test_cash_rechecked_after_fill(self):
        self.broker.cash_krw=20000
        first=self.enqueue(); self.broker.behavior='fill'; self.worker.tick(self.account)
        second=self.enqueue()
        self.worker.tick(self.account)
        self.assertEqual(self.get(second)['reason'],'INSUFFICIENT_CASH')
        self.assertEqual(len(self.broker.calls),1)

if __name__=='__main__': unittest.main()
