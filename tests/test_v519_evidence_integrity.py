import unittest
import datetime as dt
from zoneinfo import ZoneInfo
from oneil_v519_evidence_integrity import review
KST=ZoneInfo('Asia/Seoul')
class EvidenceTests(unittest.TestCase):
 def setUp(self):
  self.now=dt.datetime(2026,10,28,12,0,tzinfo=KST)
  self.b={'source':'BROKER_EQUITY_OBSERVATION','account_scope':'WHOLE_ACCOUNT','complete':True,'equity_krw':1000000,'as_of_kst':self.now.replace(hour=8,minute=50).isoformat()}
  self.c={**self.b,'equity_krw':990000,'as_of_kst':self.now.isoformat()}
  self.f={'source':'RECONCILED_EXTERNAL_CASHFLOWS','complete':True,'independently_verified':True,'net_external_cashflow_krw':0,'as_of_kst':self.now.isoformat()}
  self.h={'source':'KIS_COMPLETE_ORDER_EXECUTION_HISTORY','all_pages_read':True,'cursor_terminated':True,'broker_totals_matched':True,'query_covers_whole_day':True,'as_of_kst':self.now.isoformat(),'orders':[]}
 def runcheck(self):return review(self.b,self.c,self.f,self.h,now=self.now,env={})
 def test_valid_shapes_still_deny(self):self.assertEqual(self.runcheck()['gate'],'DENY')
 def test_zero_orders(self):self.assertEqual(self.runcheck()['orders_sent'],0)
 def test_baseline_missing(self):self.b=None;self.assertFalse(self.runcheck()['baseline_schema_valid'])
 def test_future_baseline(self):self.b['as_of_kst']=(self.now+dt.timedelta(seconds=30)).isoformat();self.assertFalse(self.runcheck()['baseline_schema_valid'])
 def test_current_stale(self):self.c['as_of_kst']=(self.now-dt.timedelta(minutes=8)).isoformat();self.assertFalse(self.runcheck()['current_schema_valid'])
 def test_flow_missing(self):self.f=None;self.assertFalse(self.runcheck()['cashflow_schema_valid'])
 def test_history_cursor_missing(self):self.h['cursor_terminated']=False;self.assertFalse(self.runcheck()['history_schema_valid'])
 def test_duplicate_order_id(self):
  x={'broker_order_id':'1','ordered_qty':2,'filled_qty':1,'state':'PARTIALLY_FILLED'}
  self.h['orders']=[x,x.copy()];self.assertFalse(self.runcheck()['history_schema_valid'])
 def test_overfill(self):
  self.h['orders']=[{'broker_order_id':'1','ordered_qty':1,'filled_qty':2,'state':'FILLED'}]
  self.assertFalse(self.runcheck()['history_schema_valid'])
 def test_loss_limit(self):self.c['equity_krw']=970000;self.assertTrue(self.runcheck()['daily_loss_limit_reached'])
 def test_disabled_required(self):
  with self.assertRaises(ValueError):review(self.b,self.c,self.f,self.h,now=self.now,env={'LIVE_ENABLED':'true'})
 def test_no_money_export(self):self.assertNotIn('990000',str(self.runcheck()))
if __name__=='__main__':unittest.main()
