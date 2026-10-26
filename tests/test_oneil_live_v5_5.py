import unittest
import datetime as dt
from zoneinfo import ZoneInfo
from oneil_live_v5_5 import evaluate,validated_timestamp

class TestV55(unittest.TestCase):
    def setUp(self): self.now=dt.datetime(2026,10,26,19,15,tzinfo=ZoneInfo('Asia/Seoul'))
    def test_missing_denied(self):
        x=evaluate(None,None,None,self.now,40,30,30)
        self.assertEqual(x['status'],'DENY');self.assertEqual(x['orders_sent'],0)
    def test_stale(self):
        self.assertFalse(validated_timestamp('2026-10-26T18:00:00+09:00',self.now))
    def test_future(self):
        self.assertFalse(validated_timestamp('2026-10-26T19:20:00+09:00',self.now))
    def test_recent(self):
        self.assertTrue(validated_timestamp('2026-10-26T19:14:00+09:00',self.now))
    def test_reconcile_fail_closed(self):
        d={'collected_at':self.now.isoformat(),'orders_sent':0,'requires_manual_review':True}
        x=evaluate(None,d,None,self.now,40,30,30)
        self.assertIn('RECONCILIATION_UNSAFE',x['inspection_issues'])
    def test_no_approval_even_with_sources(self):
        now=self.now.isoformat()
        a={'collected_at':now,'orders_sent':0,'live_enabled':False}
        b={'collected_at':now,'orders_sent':0,'requires_manual_review':False}
        c={'collected_at':now,'daily_loss_verified':True}
        x=evaluate(a,b,c,self.now,40,30,30)
        self.assertFalse(x['actual_broker_trade_authorization'])
if __name__=='__main__':unittest.main()
