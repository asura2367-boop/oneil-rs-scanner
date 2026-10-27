import unittest,datetime as dt,os
from unittest.mock import patch
from zoneinfo import ZoneInfo
from oneil_live_v5_19 import build,fresh
KST=ZoneInfo('Asia/Seoul')
class Safety(unittest.TestCase):
 def setUp(self):self.now=dt.datetime(2026,10,27,17,0,tzinfo=KST)
 def test_no_evidence_denies(self):
  r=build(None,None,None,None,self.now)
  self.assertEqual(r['gate'],'DENY');self.assertEqual(r['orders_sent'],0)
 def test_stale_rejected(self):
  self.assertFalse(fresh({'generated_at_kst':'2026-10-26T17:00:00+09:00'},self.now))
 def test_candidate_no_prices(self):
  c={'gate':'DENY','orders_sent':0,'review_candidates':[{'ticker':'085910'},{'ticker':'083470'}]}
  q={'version':'5.18','gate':'DENY','orders_sent':0,'generated_at_kst':self.now.isoformat(),'quote_price_approved_count':0}
  r=build(c,q,None,None,self.now)
  self.assertEqual(r['candidate_count'],2);self.assertIn('QUOTE_NOT_CERTIFIED_FOR_TRADING',r['issues']);self.assertEqual(r['live_orderable_qty_approved_count'],0)
 def test_flag_on_fails(self):
  with patch.dict(os.environ,{'LIVE_ENABLED':'true'}):
   with self.assertRaises(RuntimeError):build(None,None,None,None,self.now)
 def test_duplicate_candidates_rejected(self):
  c={'gate':'DENY','orders_sent':0,'review_candidates':[{'ticker':'085910'},{'ticker':'085910'}]}
  r=build(c,None,None,None,self.now)
  self.assertIn('CANDIDATE_EVIDENCE_MISSING_OR_INVALID',r['issues'])
if __name__=='__main__':unittest.main()
