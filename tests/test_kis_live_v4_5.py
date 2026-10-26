import datetime as dt
import unittest
from zoneinfo import ZoneInfo
from kis_live_v4_5 import evaluate, guard, GateBlocked
from unittest.mock import patch
N=dt.datetime(2026,10,26,11,0,tzinfo=ZoneInfo('Asia/Seoul'))
P={'mode':'READ_ONLY_PREFLIGHT_V4_1','live_enabled':False,'orders_sent':0,'generated_at_kst':N.isoformat(),'decisions':[{'ticker':'052460','status':'PREVIEW_ELIGIBLE_NO_ORDER','preview_qty':34,'preview_amount_krw':196520}]}
R={'mode':'V4_4_RECONCILE_READ_ONLY','orders_sent':0,'live_enabled':False,'broker_order_api_calls':0,'broker_readonly_queries_performed':True,'requires_manual_review':False,'unmatched_broker_order_ids':0,'unresolved_linked_ids':0,'journal_modified':False}
S={'mode':'NO_ORDER_STATE_TRACKER','halted':False,'intents':{'a':{'state':'PREVIEW_ONLY','broker_order_id':None,'filled_qty':0}}}
class TestGate(unittest.TestCase):
 def test_always_deny(self):
  a=evaluate(P,R,S,now=N);self.assertFalse(a['approved_for_live_order']);self.assertEqual(a['orders_sent'],0);self.assertIn('DAILY_LOSS_NOT_VERIFIED',a['reasons'])
 def test_unmatched(self):
  a=evaluate(P,{**R,'unmatched_broker_order_ids':1},S,now=N);self.assertIn('BROKER_ORDER_REVIEW_REQUIRED',a['reasons'])
 def test_stale(self):
  a=evaluate(P,R,S,now=N+dt.timedelta(minutes=3));self.assertIn('PREFLIGHT_STALE',a['reasons'])
 def test_cap(self):
  d={**P,'decisions':[{**P['decisions'][0],'preview_amount_krw':200001}]};self.assertIn('PER_STOCK_CAP_OR_QUANTITY_INVALID',evaluate(d,R,S,now=N)['reasons'])
 def test_duplicate(self):
  d={**P,'decisions':P['decisions']*2};self.assertIn('DUPLICATE_OR_BAD_TICKER',evaluate(d,R,S,now=N)['reasons'])
 def test_halt(self):
  self.assertIn('JOURNAL_INVALID_OR_HALTED',evaluate(P,R,{**S,'halted':True},now=N)['reasons'])
 def test_loss_not_proven(self):
  self.assertIn('DAILY_LOSS_NOT_VERIFIED',evaluate(P,R,S,now=N)['reasons'])
 def test_no_real_flags(self):
  with patch.dict('os.environ',{'LIVE_ENABLED':'true','KIS_ALLOW_ORDER':'false'}):
   with self.assertRaises(GateBlocked):guard()
 def test_same_run(self):
  self.assertIn('NOT_SAME_RUN_ARTIFACT',evaluate(P,R,S,now=N,source_mtime=[1,1],required_newer_than=2)['reasons'])
if __name__=='__main__':unittest.main()
