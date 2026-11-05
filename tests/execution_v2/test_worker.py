"""Worker orchestration tests with a non-durable test double, not DB proof."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from oneil_execution_v2.broker import SimulatedBroker
from oneil_execution_v2.domain import Blocked, Order, RiskPolicy, UNRESOLVED
from oneil_execution_v2.worker import Worker

class ScriptedStore:
    def __init__(self):
        self.clock=datetime.now(timezone.utc)
        self.account='a'*64
        self.policy=RiskPolicy('v1',100000,1000000,500000,2000000,100000)
        self.control_row={'halted':False,'policy':asdict(self.policy)}
        self.rows=[]; self.events=[]; self.locked=False; self.fail_on_observation=False
        self.conn=SimpleNamespace(closed=False)
    def now(self): return self.clock
    def control(self,account): return self.control_row
    @contextmanager
    def account_lock(self,account):
        if self.locked: yield False; return
        self.locked=True
        try: yield True
        finally: self.locked=False
    def heartbeat(self,*args,**kwargs): pass
    def unresolved(self,account): return [row for row in self.rows if row['state'] in UNRESOLVED]
    def fail_closed(self,account,reason): self.control_row['halted']=True; self.events.append(reason)
    def get(self,account,request_id): return next(row for row in self.rows if str(row['request_id'])==str(request_id))
    def claim(self,account):
        row=next((row for row in self.rows if row['state']=='QUEUED'),None)
        if row: row['state']='VALIDATING'
        return row
    def fill_requirements(self,account):
        return {str(row['request_id']):row['filled_quantity'] for row in self.rows if row['filled_quantity']}
    def reserve_attempt(self,row,day,policy,validate):
        validate(self.now(),sum(Order.from_payload(r['payload']).notional for r in self.rows if r['attempts']))
        row.update(state='SUBMISSION_UNKNOWN',attempts=1,attempt_id=uuid.uuid4(),trading_day=day)
        self.events.append('ATTEMPT_COMMITTED')
        return row
    def finish_before_send(self,row,state,reason):
        if row['state'] != 'VALIDATING': return False
        row.update(state=state,reason=reason); return True
    def apply_observation(self,row,observed):
        if self.fail_on_observation: raise RuntimeError('SIMULATED_DB_WRITE_FAILURE')
        observed.validate(str(row['request_id']),Order.from_payload(row['payload']),row['trading_day'])
        if observed.filled_quantity < row['filled_quantity']: raise Blocked('FILL_REGRESSION')
        row.update(state=observed.status,filled_quantity=observed.filled_quantity)
    def enqueue(self,**changes):
        payload=dict(account=self.account,ticker='005930',side='BUY',venue='KRX',quantity=2,
                     limit_price=10000,expires_at=self.now()+timedelta(minutes=1),policy_version='v1')
        payload.update(changes)
        row=dict(request_id=uuid.uuid4(),account=self.account,payload=Order(**payload).payload(),
                 state='QUEUED',attempts=0,filled_quantity=0)
        self.rows.append(row); return row

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.store=ScriptedStore(); self.broker=SimulatedBroker(self.store.now)
        self.worker=Worker(self.store,self.broker,core_fixture_only=True); self.account=self.store.account
    def tick(self): return self.worker.tick(self.account)
    def test_commit_precedes_broker_effect(self):
        row=self.store.enqueue()
        self.broker.submit_hook=lambda:self.assertEqual(self.store.events[-1],'ATTEMPT_COMMITTED')
        self.assertEqual(self.tick(),'ACKNOWLEDGED'); self.assertEqual(row['filled_quantity'],0)
    def test_lost_ack_reconciles_without_resend(self):
        row=self.store.enqueue(); self.broker.behavior='timeout_after_accept'
        self.assertEqual(self.tick(),'SUBMISSION_UNKNOWN'); self.assertTrue(self.store.control_row['halted'])
        self.tick(); self.assertEqual(row['state'],'ACKNOWLEDGED'); self.assertEqual(len(self.broker.calls),1)
    def test_missing_order_after_timeout_never_retries(self):
        row=self.store.enqueue(); self.broker.behavior='timeout_before_accept'; self.tick()
        for _ in range(3): self.tick()
        self.assertEqual(row['state'],'SUBMISSION_UNKNOWN'); self.assertEqual(len(self.broker.calls),1)
    def test_crash_before_ack_persistence_recovers(self):
        row=self.store.enqueue(); self.broker.behavior='crash_after_accept'
        with self.assertRaises(SystemExit): self.tick()
        self.assertEqual(row['state'],'SUBMISSION_UNKNOWN')
        self.tick(); self.assertEqual(row['state'],'ACKNOWLEDGED'); self.assertEqual(len(self.broker.calls),1)
    def test_ack_db_failure_does_not_retry_broker(self):
        row=self.store.enqueue(); self.store.fail_on_observation=True
        self.assertEqual(self.tick(),'SUBMISSION_UNKNOWN')
        self.store.fail_on_observation=False; self.tick()
        self.assertEqual(row['state'],'ACKNOWLEDGED'); self.assertEqual(len(self.broker.calls),1)
    def test_partial_fill_blocks_next_until_terminal(self):
        first=self.store.enqueue(); second=self.store.enqueue(); self.broker.behavior='partial'
        self.tick(); self.assertEqual(self.tick(),'UNRESOLVED_ORDER'); self.assertEqual(second['state'],'QUEUED')
        self.broker.advance(str(first['request_id']),status='FILLED',filled_quantity=2)
        self.tick(); self.assertEqual(first['state'],'FILLED'); self.assertEqual(len(self.broker.calls),2)
    def test_halt_still_reconciles_but_does_not_claim(self):
        row=self.store.enqueue(); self.store.control_row['halted']=True
        self.assertEqual(self.tick(),'HALTED'); self.assertEqual(row['state'],'QUEUED'); self.assertEqual(self.broker.calls,[])
    def test_bad_request_does_not_discard_other_request(self):
        first=self.store.enqueue(limit_price=12000); second=self.store.enqueue()
        self.assertEqual(self.tick(),'BLOCKED'); self.assertEqual(self.tick(),'ACKNOWLEDGED')
        self.assertEqual(first['state'],'BLOCKED'); self.assertEqual(second['state'],'ACKNOWLEDGED')
    def test_history_failure_prevents_claim(self):
        row=self.store.enqueue(); self.broker.history_complete=False
        self.assertEqual(self.tick(),'RECONCILIATION_BLOCKED'); self.assertEqual(row['state'],'QUEUED')
    def test_history_rechecked_at_admission(self):
        row=self.store.enqueue(); original=self.broker.quote
        def delayed_quote(order): self.store.clock+=timedelta(seconds=11); return original(order)
        with patch.object(self.broker,'quote',side_effect=delayed_quote): self.assertEqual(self.tick(),'BLOCKED')
        self.assertEqual(row['reason'],'HISTORY_STALE'); self.assertEqual(self.broker.calls,[])
    def test_causal_account_evidence_required(self):
        self.store.enqueue(); self.broker.behavior='fill'; self.tick()
        self.broker.balances[self.account]['fills']={}; row=self.store.enqueue()
        self.assertEqual(self.tick(),'BLOCKED'); self.assertEqual(row['reason'],'ACCOUNT_NOT_CAUSALLY_RECONCILED')
    def test_uncertain_admission_commit_does_not_report_safe_rejection(self):
        row=self.store.enqueue(); original=self.store.reserve_attempt
        def uncertain(*args):
            original(*args)
            raise RuntimeError('SIMULATED_COMMIT_RESPONSE_LOST')
        with patch.object(self.store,'reserve_attempt',side_effect=uncertain):
            self.assertEqual(self.tick(),'SUBMISSION_UNKNOWN')
        self.assertEqual(row['state'],'SUBMISSION_UNKNOWN')
        self.assertTrue(self.store.control_row['halted'])
        self.assertEqual(self.broker.calls,[])

    def test_busy_worker_does_not_touch_broker(self):
        self.store.enqueue(); self.store.locked=True
        self.assertEqual(self.tick(),'ACCOUNT_BUSY'); self.assertEqual(self.broker.calls,[])

if __name__=='__main__': unittest.main()
