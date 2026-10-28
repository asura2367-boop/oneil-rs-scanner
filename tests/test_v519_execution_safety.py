import datetime as dt
import os
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
from oneil_v519_execution_safety import inspect, transition, SafetyError
KST=ZoneInfo('Asia/Seoul')
NOW=dt.datetime(2026,10,28,10,0,tzinfo=KST)
C={'mode':'LIVE_INTENTS_PREVIEW_ONLY','gate':'DENY','orders_sent':0,'generated_at_kst':NOW.isoformat(), 'intents':[{'ticker':'085910'}]}
A={'gate':'DENY','orders_sent':0,'as_of_kst':NOW.isoformat(),'broker_status':'READ_ONLY_OBSERVED'}
Q={'version':'5.18','orders_sent':0,'generated_at_kst':NOW.isoformat()}
R=None
class Tests(unittest.TestCase):
    def test_fails_closed(self):
        with patch.dict(os.environ,{'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'}):
            x=inspect(C,A,Q,R,NOW)
        self.assertEqual(x['gate'],'DENY');self.assertEqual(x['orders_sent'],0)
        self.assertIn('DAILY_LOSS_BASELINE_OR_CASHFLOWS_NOT_VERIFIED',x['blocks'])
    def test_duplicate_candidates_blocked(self):
        c={**C,'intents':[{'ticker':'085910'},{'ticker':'085910'}]}
        self.assertEqual(inspect(c,A,Q,R,NOW)['candidate_count'],0)
    def test_old_account_blocked(self):
        a={**A,'as_of_kst':(NOW-dt.timedelta(minutes=6)).isoformat()}
        self.assertIn('ACCOUNT_OBSERVATION_MISSING_OR_STALE',inspect(C,a,Q,R,NOW)['blocks'])
    def test_missing_quote(self):
        self.assertIn('QUOTE_OBSERVATION_MISSING_OR_STALE',inspect(C,A,None,R,NOW)['blocks'])
    def test_live_flag_blocked(self):
        with patch.dict(os.environ,{'LIVE_ENABLED':'true'}):
            with self.assertRaises(SafetyError): inspect(C,A,Q,R,NOW)
    def test_unknown_state_no_resubmit(self):
        x={'state':'PREVIEW_ONLY','requested_qty':10,'filled_qty':0,'broker_order_id':None}
        z=transition(x,'SUBMISSION_UNKNOWN')
        self.assertFalse(z['automatic_resubmission_allowed'])
        with self.assertRaises(SafetyError):transition(z,'SUBMISSION_UNKNOWN')
    def test_partial_fill(self):
        x={'state':'SUBMISSION_UNKNOWN','requested_qty':10,'filled_qty':0,'broker_order_id':None}
        y=transition(x,'PART_FILLED',broker_order_id='1234567',cumulative_filled_qty=3)
        self.assertEqual(y['filled_qty'],3)
        with self.assertRaises(SafetyError):transition(y,'FILLED',cumulative_filled_qty=9)
    def test_broker_id_mismatch(self):
        x={'state':'PART_FILLED','requested_qty':10,'filled_qty':3,'broker_order_id':'123'}
        with self.assertRaises(SafetyError):transition(x,'FILLED',broker_order_id='789',cumulative_filled_qty=10)
    def test_overfill(self):
        x={'state':'ACKNOWLEDGED','requested_qty':10,'filled_qty':0,'broker_order_id':'123'}
        with self.assertRaises(SafetyError):transition(x,'PART_FILLED',cumulative_filled_qty=11)
    def test_terminal_cannot_reopen(self):
        x={'state':'FILLED','requested_qty':10,'filled_qty':10,'broker_order_id':'123'}
        with self.assertRaises(SafetyError):transition(x,'ACKNOWLEDGED')
if __name__=='__main__':unittest.main()
