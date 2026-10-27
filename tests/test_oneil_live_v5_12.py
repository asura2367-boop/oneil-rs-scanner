import unittest
import datetime as dt
from zoneinfo import ZoneInfo
from oneil_live_v5_12 import inspect
NOW=dt.datetime(2026,10,27,12,30,tzinfo=ZoneInfo('Asia/Seoul'))
INTENTS={'mode':'LIVE_INTENTS_PREVIEW_ONLY','gate':'DENY','orders_sent':0,'intents':[{'ticker':'085910','intent_id':'test','status':'REVIEW_ONLY_UNPRICED','requested_side':'BUY','quantity':None,'price_krw':None}]}
class SafetyTests(unittest.TestCase):
 def test_missing_snapshot_fails_closed(self):
  o=inspect(INTENTS,{},NOW)
  self.assertIn('BROKER_SNAPSHOT_MISSING',o['blocking_reasons'])
  self.assertEqual(o['orders_sent'],0)
 def test_recent_unattested_not_authorizing(self):
  s={'generated_at_kst':NOW.isoformat(),'holdings':[],'unfilled_orders':[],'orderable_cash_krw':100000}
  o=inspect(INTENTS,s,NOW)
  self.assertEqual(o['snapshot_status'],'RECENT_UNATTESTED_INPUT')
  self.assertFalse(o['account_values_used_to_authorize'])
  self.assertIsNone(o['real_order_quantities'])
 def test_holdings_blocked(self):
  s={'generated_at_kst':NOW.isoformat(),'holdings':['085910'],'unfilled_orders':[],'orderable_cash_krw':500000}
  o=inspect(INTENTS,s,NOW)
  self.assertIn('ALREADY_HELD',o['candidate_checks'][0]['checks'])
 def test_stale_rejected(self):
  s={'generated_at_kst':(NOW-dt.timedelta(minutes=3)).isoformat(),'holdings':[],'unfilled_orders':[],'orderable_cash_krw':500000}
  self.assertIn('BROKER_SNAPSHOT_STALE_OR_INVALID',inspect(INTENTS,s,NOW)['blocking_reasons'])
 def test_unsafe_intent_rejected(self):
  with self.assertRaises(ValueError): inspect({'gate':'ALLOW'}, {}, NOW)
if __name__=='__main__':unittest.main()
