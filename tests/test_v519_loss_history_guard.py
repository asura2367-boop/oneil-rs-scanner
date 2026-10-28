import datetime as dt
import unittest
from zoneinfo import ZoneInfo
from oneil_v519_loss_history_guard import analyze
KST=ZoneInfo('Asia/Seoul')
class TestGuard(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,10,28,12,0,tzinfo=KST)
        self.equity={'schema_version':1,'market_date':'2026-10-28','scope':'WHOLE_ACCOUNT',
           'evidence_verified':True,'baseline_time_kst':'2026-10-28T08:50:00+09:00',
           'current_time_kst':'2026-10-28T11:59:00+09:00',
           'baseline_equity_krw':1000000,'current_equity_krw':1000000,
           'net_external_cashflow_krw':0,'cashflows_reconciled':True,
           'holdings_reconciled':True,'fees_included':True,'source_complete':True}
        self.history={'source':'BROKER_ORDER_HISTORY','verified':True,'complete':True,
          'orders_sent':0,'generated_at_kst':self.now.isoformat(),
          'orders':[{'broker_order_id':'A1','state':'PART_FILLED','requested_qty':10,'filled_qty':4}]}
    def test_missing(self):
        r=analyze(None,None,self.now,{});self.assertEqual(r['gate'],'DENY');self.assertFalse(r['daily_loss_evidence_valid'])
    def test_valid_synthetic_still_deny(self):
        r=analyze(self.equity,self.history,self.now,{});self.assertTrue(r['daily_loss_evidence_valid']);self.assertTrue(r['order_history_structure_valid']);self.assertEqual(r['gate'],'DENY')
    def test_loss_limit(self):
        self.equity['current_equity_krw']=979999
        self.assertTrue(analyze(self.equity,self.history,self.now,{})['daily_loss_limit_reached'])
    def test_external_flow_reconciliation(self):
        self.equity['cashflows_reconciled']=False
        self.assertFalse(analyze(self.equity,self.history,self.now,{})['daily_loss_evidence_valid'])
    def test_duplicate_ids(self):
        self.history['orders']*=2
        self.assertFalse(analyze(self.equity,self.history,self.now,{})['order_history_structure_valid'])
    def test_overfill(self):
        self.history['orders'][0]['filled_qty']=11
        self.assertFalse(analyze(self.equity,self.history,self.now,{})['order_history_structure_valid'])
    def test_stale(self):
        self.history['generated_at_kst']=(self.now-dt.timedelta(minutes=6)).isoformat()
        self.assertFalse(analyze(self.equity,self.history,self.now,{})['order_history_structure_valid'])
    def test_no_order(self):
        r=analyze(self.equity,self.history,self.now,{});self.assertEqual(r['orders_sent'],0)
    def test_live_switch(self):
        with self.assertRaises(ValueError):analyze(None,None,self.now,{'LIVE_ENABLED':'true'})
    def test_order_switch(self):
        with self.assertRaises(ValueError):analyze(None,None,self.now,{'KIS_ALLOW_ORDER':'true'})
if __name__=='__main__':unittest.main()
