import datetime as dt
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
from oneil_v519_pretrade_audit import audit
KST=ZoneInfo('Asia/Seoul')
class Tests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,10,28,12,tzinfo=KST);t=self.now.isoformat()
        self.c={'generated_at_kst':t,'mode':'LIVE_INTENTS_PREVIEW_ONLY','gate':'DENY','orders_sent':0,'intents':[{'ticker':'123456','status':'REVIEW_ONLY_UNPRICED'}]}
        self.a={'as_of_kst':t,'broker_status':'READ_ONLY_OBSERVED','gate':'DENY','orders_sent':0}
        self.b={'generated_at_kst':t,'status':'OBSERVATION_ATTEMPTED','gate':'DENY','orders_sent':0}
        self.q={'generated_at_kst':t,'version':'5.18','orders_sent':0}
        self.r={'generated_at_kst':t,'orders_sent':0}
    def run_check(self,history=None):return audit(self.c,self.a,self.b,self.q,self.r,history,self.now,env={'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'false'})
    def test_clean_diagnostics_still_deny(self):
        o=self.run_check();self.assertEqual(o['gate'],'DENY');self.assertEqual(o['orders_sent'],0)
        self.assertIn('EXECUTABLE_QUOTE_NOT_ATTESTED',o['block_codes'])
        self.assertIn('DAILY_LOSS_INDEPENDENT_RECONCILIATION_REQUIRED',o['block_codes'])
    def test_stale_account(self):
        self.a['as_of_kst']=(self.now-dt.timedelta(minutes=6)).isoformat();self.assertIn('ACCOUNT_NOT_VERIFIED',self.run_check()['block_codes'])
    def test_duplicate_codes(self):
        self.c['intents']*=2;self.assertEqual(self.run_check()['candidate_count'],0)
    def test_invalid_code(self):
        self.c['intents'][0]['ticker']='../bad';self.assertEqual(self.run_check()['candidate_count'],0)
    def test_missing_quote(self):
        self.q=None;self.assertIn('QUOTE_DIAGNOSTIC_MISSING_OR_STALE',self.run_check()['block_codes'])
    def test_future_timestamp(self):
        self.a['as_of_kst']=(self.now+dt.timedelta(seconds=10)).isoformat();self.assertIn('ACCOUNT_NOT_VERIFIED',self.run_check()['block_codes'])
    def test_history_not_equivalent_to_order_authorization(self):
        h={'generated_at_kst':self.now.isoformat(),'source':'BROKER_ORDER_HISTORY','verified':True}
        self.assertIn('AUTOMATIC_RETRY_AND_ORDER_TRANSMISSION_DISABLED',self.run_check(h)['block_codes'])
    def test_missing_cash_amount_not_leaked(self):
        self.assertNotIn('cash_krw',str(self.run_check()))
    def test_live_switch_block(self):
        with self.assertRaises(ValueError):audit(self.c,self.a,self.b,self.q,self.r,None,self.now,env={'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'false'})
    def test_order_switch_block(self):
        with self.assertRaises(ValueError):audit(self.c,self.a,self.b,self.q,self.r,None,self.now,env={'LIVE_ENABLED':'false','KIS_ALLOW_ORDER':'true'})
    def test_zero_candidates(self):
        self.c['intents']=[];self.assertIn('CANDIDATE_NOT_VERIFIED',self.run_check()['block_codes'])
    def test_negative_order_count(self):
        self.c['orders_sent']=-1;self.assertEqual(self.run_check()['candidate_count'],0)
if __name__=='__main__':unittest.main()
