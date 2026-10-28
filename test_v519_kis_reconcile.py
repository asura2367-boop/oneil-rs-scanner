import datetime as dt
import os
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
from oneil_v519_kis_reconcile import reconcile, ReconcileError

KST=ZoneInfo('Asia/Seoul')
class Tests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,10,28,10,0,tzinfo=KST)
        self.i={'mode':'LIVE_INTENTS_PREVIEW_ONLY','gate':'DENY','orders_sent':0,
                'generated_at_kst':self.now.isoformat(),'intents':[{'ticker':'123456','status':'REVIEW_ONLY_UNPRICED','quantity':None,'price_krw':None}]}
        self.a={'version':'5.13','gate':'DENY','orders_sent':0,'broker_status':'READ_ONLY_OBSERVED',
                'as_of_kst':self.now.isoformat(),'checks':[{'ticker':'123456','held':'NO','unfilled':'NO'}]}
        self.b={'version':'5.19-indicative-power','gate':'DENY','orders_sent':0,'status':'OBSERVATION_ATTEMPTED',
                'generated_at_kst':self.now.isoformat(),'broker_buying_power_verified':False,
                'checks':[{'ticker':'123456','status':'INDICATIVE_POWER_OBSERVED_UNATTESTED'}]}
    def run_check(self):return reconcile(self.i,self.a,self.b,self.now)
    def test_consistent_but_never_authorized(self):
        r=self.run_check();self.assertEqual(r['gate'],'DENY');self.assertEqual(r['orders_sent'],0)
        self.assertEqual(r['checks'][0]['status'],'CONSISTENT_READ_ONLY_NOT_AUTHORIZED')
    def test_missing_buying(self):
        self.b=None;self.assertIn('BUYING_POWER_NOT_OBSERVED',self.run_check()['blocking_reasons'])
    def test_outdated(self):
        self.a['as_of_kst']=(self.now-dt.timedelta(minutes=6)).isoformat()
        self.assertFalse(self.run_check()['time_coherent'])
    def test_mismatch(self):
        self.b['checks'][0]['ticker']='654321';self.assertFalse(self.run_check()['candidate_sets_equal'])
    def test_duplicate(self):
        self.b['checks']*=2;self.assertFalse(self.run_check()['candidate_sets_equal'])
    def test_held_blocks(self):
        self.a['checks'][0]['held']='YES';self.b['checks'][0]['status']='ALREADY_HELD'
        self.assertEqual(self.run_check()['checks'][0]['status'],'BLOCKED_EXISTING_POSITION_OR_PENDING')
    def test_conflicting_observations(self):
        self.b['checks'][0]['status']='UNFILLED_ORDER'
        self.assertEqual(self.run_check()['checks'][0]['status'],'BLOCKED_BROKER_OBSERVATION_CONFLICT')
    def test_unfilled_blocks(self):
        self.a['checks'][0]['unfilled']='YES';self.b['checks'][0]['status']='UNFILLED_ORDER'
        self.assertEqual(self.run_check()['checks'][0]['status'],'BLOCKED_EXISTING_POSITION_OR_PENDING')
    def test_no_cash_amount_leak(self):
        self.assertNotIn('ord_psbl_cash',str(self.run_check()))
    def test_live_switch_block(self):
        with patch.dict(os.environ,{'LIVE_ENABLED':'true'}):
            with self.assertRaises(ReconcileError):self.run_check()
    def test_naive_clock_block(self):
        with self.assertRaises(ReconcileError):reconcile(self.i,self.a,self.b,dt.datetime(2026,10,28))
    def test_never_assume_account(self):
        self.a['broker_status']='NOT_REQUESTED';self.assertFalse(self.run_check()['checks'][0]['approved'])
if __name__=='__main__':unittest.main()
